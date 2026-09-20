"""A small browser front end over the existing renderer.

The renderer is the product; this module is a thin HTTP shell that turns a
``multipart/form-data`` upload into a :class:`~ascii_art.render.RenderOptions`
and hands the rendered result back to the browser.  Uploads are decoded and
rendered in memory: nothing is written to disk and nothing is sent to any
external service.  The request body is size-capped before it is read.

The form uses the CLI's flag names verbatim (``mode``, ``chars``, ``color``,
``dither``, ...) so the browser interface does not invent a second vocabulary;
see :func:`build_options` for the mapping back to ``RenderOptions``.
"""

from __future__ import annotations

import io
import json
import os
import sys
from email.parser import BytesParser
from email.policy import default as email_default
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, Optional, Tuple
from urllib.parse import urlparse

from PIL import Image

from .errors import AsciiArtError, UsageError
from .filters import FilterSpec, apply_filters, validate as validate_filters
from .geometry import compute_geometry, parse_font_ratio
from .loader import load_image
from .output import FORMATS, format_canvas, to_text
from .palette import auto_depth_for_format, parse_colour
from .render import (
    ALPHA_MODES,
    BACKGROUNDS,
    COLOR_DEPTHS,
    MODES,
    RenderOptions,
    render,
)

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8080
DEFAULT_MAX_BODY_BYTES = 20 * 1024 * 1024  # 20 MiB, the upload cap
DEFAULT_MAX_PIXELS = 40_000_000  # 40 megapixels after decode
DEFAULT_MAX_CELLS = 4_000_000  # cells in the output grid

_PROG = "ascii-art-web"


class _HttpError(Exception):
    """An error with an HTTP status, reported as a JSON ``{ok: false}`` body."""

    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


# ---------------------------------------------------------------------------
# Form -> RenderOptions


def _text(fields: Dict[str, str], name: str) -> str:
    return fields.get(name, "")


def _int(fields: Dict[str, str], name: str, default: Optional[int]) -> Optional[int]:
    raw = _text(fields, name).strip()
    if raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise UsageError(f"--{name} must be an integer") from None


def _float(fields: Dict[str, str], name: str, default: float) -> float:
    raw = _text(fields, name).strip()
    if raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        raise UsageError(f"--{name} must be a number") from None


def _choice(
    fields: Dict[str, str], name: str, choices: Tuple[str, ...], default: str
) -> str:
    value = _text(fields, name).strip()
    if value == "":
        return default
    if value not in choices:
        raise UsageError(f"--{name} {value!r} must be one of {', '.join(choices)}")
    return value


def _bool(fields: Dict[str, str], name: str) -> bool:
    # A checked checkbox submits value="on"; an unchecked one is absent.
    if name not in fields:
        return False
    return fields[name].strip().lower() in ("on", "true", "1", "yes", "")


def _check_text_can_carry_colour(color: str, fmt: str) -> None:
    if fmt == "text" and color not in ("none", "auto"):
        raise UsageError(
            f"--format text is plain UTF-8 and cannot carry colour; drop "
            f"--color {color} or use --format ansi"
        )


def build_options(
    fields: Dict[str, str],
) -> Tuple[RenderOptions, str, str, bool, bool]:
    """Build a :class:`RenderOptions` from form fields named after CLI flags.

    Returns ``(options, format, depth, fg_only, polite)``.
    """

    mode = _choice(fields, "mode", MODES, "ramp")
    chars = _text(fields, "chars") or None  # never strip: a leading space is a glyph
    ramp_order = _choice(fields, "ramp", ("measured", "as-given"), "measured")
    edge_threshold = _float(fields, "edge_threshold", 0.15)
    color = _choice(fields, "color", COLOR_DEPTHS, "auto")
    dither = _choice(fields, "dither", ("none", "ordered", "diffusion", "noise"), "none")
    seed = _int(fields, "seed", None)
    background = _choice(fields, "background", BACKGROUNDS, "auto")
    invert = _bool(fields, "invert")
    fg_only = _bool(fields, "fg_only")
    alpha = _choice(fields, "alpha", ALPHA_MODES, "transparent")
    alpha_bg_text = _text(fields, "alpha_bg").strip()
    alpha_bg = parse_colour(alpha_bg_text) if alpha_bg_text else None
    alpha_threshold = _int(fields, "alpha_threshold", 128)
    width = _int(fields, "width", None)
    height = _int(fields, "height", None)
    size = _text(fields, "size").strip() or None
    scale = _text(fields, "scale").strip() or None
    fit = _bool(fields, "fit")
    stretch = _bool(fields, "stretch")
    font_ratio = parse_font_ratio(_text(fields, "font_ratio").strip() or "1/2")
    brightness = _float(fields, "brightness", 1.0)
    contrast = _float(fields, "contrast", 1.0)
    gamma = _float(fields, "gamma", 1.0)
    rotate = _int(fields, "rotate", 0)
    flip_x = _bool(fields, "flip_x")
    flip_y = _bool(fields, "flip_y")
    fmt = _choice(fields, "format", FORMATS, "text")
    polite = _bool(fields, "polite")

    depth = auto_depth_for_format(fmt) if color == "auto" else color

    filters = FilterSpec(
        brightness=brightness,
        contrast=contrast,
        gamma=gamma,
        invert=invert,
        rotate=rotate,
        flip_x=flip_x,
        flip_y=flip_y,
    )
    validate_filters(filters)

    options = RenderOptions(
        mode=mode,
        chars=chars,
        ramp_order=ramp_order,
        color=depth,
        dither=dither,
        seed=seed,
        background=background,
        invert=invert,
        fg_only=fg_only,
        alpha=alpha,
        alpha_bg=alpha_bg,
        alpha_threshold=alpha_threshold,
        edge_threshold=edge_threshold,
        width=width,
        height=height,
        size=size,
        scale=scale,
        fit=fit,
        stretch=stretch,
        font_ratio=font_ratio,
        filters=filters,
        font_path=None,
        is_tty=False,
        term=None,
    )
    options.validate()
    # After render-option validation so a specific mode/colour conflict (e.g.
    # ``half`` with ``color 2``) wins over the generic text-format conflict.
    _check_text_can_carry_colour(color, fmt)
    return options, fmt, depth, fg_only, polite


def _check_cell_budget(
    image: Image.Image, options: RenderOptions, max_cells: int
) -> None:
    """Refuse an output grid larger than ``max_cells`` before allocating it."""

    filtered = apply_filters(image, options.filters)
    geometry = compute_geometry(
        filtered.width,
        filtered.height,
        width=options.width,
        height=options.height,
        size=options.size,
        scale=options.scale,
        fit=options.fit,
        stretch=options.stretch,
        font_ratio=options.font_ratio,
        term=options.term,
        is_tty=options.is_tty,
    )
    cells = geometry.cols * geometry.rows
    if cells > max_cells:
        raise _HttpError(
            HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            f"output grid of {cells} cells exceeds the cap of {max_cells} cells "
            f"(ASCII_ART_MAX_CELLS); reduce width, height, size or scale",
        )


def _decode_upload(payload: bytes, max_pixels: int) -> Image.Image:
    """Decode uploaded bytes with the library's own stdin path."""

    try:
        image = load_image("-", stdin=io.BytesIO(payload))
    except AsciiArtError as exc:
        # Reuse the library's decode diagnostics, but drop its ``<stdin>``
        # label, which is a CLI concept and means nothing in a browser.
        message = str(exc)
        if message.startswith("<stdin>: "):
            message = "uploaded image: " + message[len("<stdin>: "):]
        raise _HttpError(HTTPStatus.BAD_REQUEST, message) from exc
    except Image.DecompressionBombError as exc:
        raise _HttpError(
            HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "image too large to decode"
        ) from exc
    if image.width * image.height > max_pixels:
        raise _HttpError(
            HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            f"image too large: {image.width}x{image.height} is "
            f"{image.width * image.height} pixels (limit {max_pixels})",
        )
    return image


def _output_meta(fmt: str) -> Tuple[str, str]:
    if fmt == "html":
        return "text/html", "ascii-art.html"
    if fmt == "ansi":
        return "text/plain", "ascii-art.ans"
    return "text/plain", "ascii-art.txt"


def render_payload(
    payload: bytes, fields: Dict[str, str], *, max_pixels: int, max_cells: int
) -> Dict[str, str]:
    """Render uploaded bytes and return the JSON-serialisable result."""

    image = _decode_upload(payload, max_pixels)
    options, fmt, depth, fg_only, polite = build_options(fields)
    _check_cell_budget(image, options, max_cells)
    canvas = render(image, options, color_depth=depth)
    output = format_canvas(canvas, fmt, depth, fg_only=fg_only, polite=polite)
    preview = to_text(canvas)
    mime, filename = _output_meta(fmt)
    return {
        "output": output,
        "preview": preview,
        "format": fmt,
        "mime": mime,
        "filename": filename,
    }


# ---------------------------------------------------------------------------
# Multipart parsing (stdlib only; ``cgi`` is gone in Python 3.13)


def parse_form(
    body: bytes, content_type: str
) -> Tuple[Dict[str, str], Dict[str, bytes]]:
    """Parse a ``multipart/form-data`` body into text fields and file parts."""

    if not content_type or not content_type.lower().startswith("multipart/form-data"):
        raise _HttpError(
            HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "expected multipart/form-data"
        )
    if "boundary=" not in content_type:
        raise _HttpError(
            HTTPStatus.BAD_REQUEST, "multipart/form-data is missing its boundary"
        )
    try:
        msg = BytesParser(policy=email_default).parsebytes(
            b"Content-Type: " + content_type.encode("latin-1") + b"\r\n\r\n" + body
        )
    except Exception as exc:  # pragma: no cover - defensive
        raise _HttpError(HTTPStatus.BAD_REQUEST, f"could not parse form data: {exc}") from exc

    fields: Dict[str, str] = {}
    files: Dict[str, bytes] = {}
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        if part.get_filename() is not None:
            files[name] = part.get_payload(decode=True) or b""
        else:
            charset = part.get_content_charset() or "utf-8"
            raw = part.get_payload(decode=True) or b""
            fields[name] = raw.decode(charset, "replace")
    return fields, files


# ---------------------------------------------------------------------------
# HTTP server


class WebHandler(BaseHTTPRequestHandler):
    server_version = _PROG + "/1.0"
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send_bytes(
                HTTPStatus.OK, "text/html; charset=utf-8", INDEX_HTML.encode("utf-8")
            )
        elif path == "/healthz":
            self._send_bytes(HTTPStatus.OK, "text/plain; charset=utf-8", b"ok\n")
        elif path == "/favicon.ico":
            self._send_bytes(HTTPStatus.NO_CONTENT, "image/x-icon", b"")
        elif path == "/render":
            self._send_json(
                HTTPStatus.METHOD_NOT_ALLOWED,
                {"ok": False, "error": "use POST /render"},
                extra_headers={"Allow": "POST"},
            )
        else:
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/render":
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
            return
        try:
            self._handle_render()
        except _HttpError as exc:
            self._send_json(exc.status, {"ok": False, "error": exc.message})
        except AsciiArtError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
        except Image.DecompressionBombError:
            self._send_json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"ok": False, "error": "image too large to decode"},
            )
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:  # pragma: no cover - defensive
            import traceback

            traceback.print_exc()
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": "internal error"}
            )

    def _handle_render(self) -> None:
        content_type = self.headers.get("Content-Type", "")
        body = self._read_body()
        fields, files = parse_form(body, content_type)
        payload = files.get("image")
        if payload is None or not payload:
            raise _HttpError(
                HTTPStatus.BAD_REQUEST,
                "no image uploaded: expected a file field named 'image'",
            )
        result = render_payload(
            payload,
            fields,
            max_pixels=self.server.max_pixels,
            max_cells=self.server.max_cells,
        )
        self._send_json(HTTPStatus.OK, {"ok": True, **result})

    def _read_body(self) -> bytes:
        length_header = self.headers.get("Content-Length")
        if length_header is None:
            raise _HttpError(
                HTTPStatus.LENGTH_REQUIRED, "Content-Length header is required"
            )
        try:
            length = int(length_header)
        except ValueError:
            raise _HttpError(HTTPStatus.BAD_REQUEST, "invalid Content-Length header") from None
        if length < 0:
            raise _HttpError(HTTPStatus.BAD_REQUEST, "invalid Content-Length header")
        if length > self.server.max_body_bytes:
            self.close_connection = True
            raise _HttpError(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                f"upload too large: {length} bytes exceeds the limit of "
                f"{self.server.max_body_bytes} bytes",
            )
        return self.rfile.read(length)

    def _send_bytes(
        self,
        status: HTTPStatus,
        content_type: str,
        body: bytes,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for key, value in (extra_headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def _send_json(
        self,
        status: HTTPStatus,
        payload: dict,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> None:
        body = json.dumps(payload).encode("utf-8")
        self._send_bytes(status, "application/json; charset=utf-8", body, extra_headers)


class WebServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: Tuple[str, int],
        RequestHandlerClass,
        *,
        max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
        max_pixels: int = DEFAULT_MAX_PIXELS,
        max_cells: int = DEFAULT_MAX_CELLS,
    ) -> None:
        self.max_body_bytes = max_body_bytes
        self.max_pixels = max_pixels
        self.max_cells = max_cells
        super().__init__(server_address, RequestHandlerClass)


def make_server(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    *,
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
    max_pixels: int = DEFAULT_MAX_PIXELS,
    max_cells: int = DEFAULT_MAX_CELLS,
) -> WebServer:
    return WebServer(
        (host, port),
        WebHandler,
        max_body_bytes=max_body_bytes,
        max_pixels=max_pixels,
        max_cells=max_cells,
    )


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        print(f"{_PROG}: {name} must be an integer, got {raw!r}", file=sys.stderr)
        raise SystemExit(2) from None


def main() -> None:
    host = os.environ.get("ASCII_ART_HOST", DEFAULT_HOST)
    port = _env_int("ASCII_ART_PORT", DEFAULT_PORT)
    max_body_bytes = _env_int("ASCII_ART_MAX_BODY_BYTES", DEFAULT_MAX_BODY_BYTES)
    max_pixels = _env_int("ASCII_ART_MAX_PIXELS", DEFAULT_MAX_PIXELS)
    max_cells = _env_int("ASCII_ART_MAX_CELLS", DEFAULT_MAX_CELLS)

    # Pillow refuses to even open a decompression bomb above this many pixels;
    # set it once here rather than per-request so concurrent threads never race.
    Image.MAX_IMAGE_PIXELS = max_pixels

    server = make_server(
        host,
        port,
        max_body_bytes=max_body_bytes,
        max_pixels=max_pixels,
        max_cells=max_cells,
    )
    print(
        f"{_PROG}: listening on http://{host}:{port} "
        f"(max upload {max_body_bytes} bytes, max {max_pixels} pixels, "
        f"max {max_cells} cells)",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        pass
    finally:
        server.server_close()


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ascii-art</title>
<style>
  body { font-family: system-ui, -apple-system, sans-serif; margin: 0 auto; padding: 1rem; line-height: 1.45; max-width: 62rem; }
  h1 { font-size: 1.35rem; margin: 0 0 0.25rem; }
  .note { color: #777; font-size: 0.85rem; margin: 0 0 1rem; }
  #drop { border: 2px dashed #888; border-radius: 8px; padding: 1.4rem; text-align: center; cursor: pointer; }
  #drop.dragover { border-color: #4af; background: rgba(64,160,255,0.08); }
  #drop-text { font-weight: 600; }
  #preview { max-width: 180px; max-height: 140px; display: block; margin: 0.6rem auto 0; }
  fieldset { border: 1px solid #888; border-radius: 6px; margin: 0.7rem 0; padding: 0.55rem 0.8rem 0.7rem; }
  legend { font-weight: 600; padding: 0 0.3rem; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(14rem, 1fr)); gap: 0.15rem 1.1rem; }
  label { display: flex; align-items: baseline; gap: 0.4rem; margin: 0.28rem 0; flex-wrap: wrap; }
  label > span { min-width: 9.5rem; color: #444; }
  input[type=number], input[type=text], select { font: inherit; padding: 0.18rem 0.3rem; max-width: 100%; box-sizing: border-box; }
  input[type=number] { width: 7rem; }
  input[type=text] { flex: 1; min-width: 8rem; }
  select { flex: 1; min-width: 8rem; }
  .check { display: flex; align-items: center; gap: 0.4rem; }
  .check input { width: auto; }
  .check span { min-width: 0 !important; }
  button { font: inherit; padding: 0.4rem 0.9rem; margin: 0.5rem 0.5rem 0 0; cursor: pointer; }
  #status { color: #777; min-height: 1.2em; }
  .error { color: #b33; border: 1px solid #b33; border-radius: 6px; padding: 0.5rem 0.8rem; margin: 0.5rem 0; white-space: pre-wrap; }
  pre#result { border: 1px solid #888; border-radius: 6px; padding: 0.7rem; overflow: auto; white-space: pre; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 0.72rem; line-height: 1.05; max-height: 70vh; }
  iframe#result-frame { width: 100%; height: 62vh; border: 1px solid #888; border-radius: 6px; background: #fff; }
  @media (max-width: 42rem) {
    body { padding: 0.5rem; }
    label > span { min-width: 8rem; }
    .grid { grid-template-columns: 1fr; }
  }
</style>
</head>
<body>
<h1>ascii-art</h1>
<p class="note">Upload an image, set the parameters, and render it to ASCII/Unicode art.
Uploads are processed in memory on the server — nothing is written to disk or sent anywhere else.</p>

<div id="drop" tabindex="0" role="button" aria-label="Choose or drop an image">
  <input type="file" id="file" accept="image/*" hidden>
  <div id="drop-text">Choose an image or drop it here</div>
  <img id="preview" alt="" hidden>
</div>

<form id="form">
  <fieldset>
    <legend>Geometry</legend>
    <div class="grid">
      <label><span>width (cells)</span><input type="number" name="width" min="1" step="1" placeholder="80"></label>
      <label><span>height (cells)</span><input type="number" name="height" min="1" step="1" placeholder="auto"></label>
      <label><span>size (WxH)</span><input type="text" name="size" placeholder="80x40"></label>
      <label><span>scale (N|max)</span><input type="text" name="scale" placeholder="1"></label>
      <label class="check"><input type="checkbox" name="fit" value="on"><span>fit (preserve aspect)</span></label>
      <label class="check"><input type="checkbox" name="stretch" value="on"><span>stretch (fill exactly)</span></label>
      <label><span>font ratio (W/H)</span><input type="text" name="font_ratio" value="1/2"></label>
    </div>
  </fieldset>

  <fieldset>
    <legend>Render mode</legend>
    <div class="grid">
      <label><span>mode</span><select name="mode">
        <option value="ramp" selected>ramp</option>
        <option value="braille">braille</option>
        <option value="block">block</option>
        <option value="half">half (colour only)</option>
        <option value="edges">edges</option>
      </select></label>
      <label><span>chars</span><input type="text" name="chars" placeholder="default measured ramp"></label>
      <label><span>ramp order</span><select name="ramp">
        <option value="measured" selected>measured</option>
        <option value="as-given">as-given</option>
      </select></label>
      <label><span>edge threshold</span><input type="number" name="edge_threshold" value="0.15" step="0.01" min="0"></label>
    </div>
  </fieldset>

  <fieldset>
    <legend>Tone, colour and dithering</legend>
    <div class="grid">
      <label><span>color</span><select name="color">
        <option value="auto" selected>auto (none)</option>
        <option value="none">none</option>
        <option value="2">2</option>
        <option value="8">8</option>
        <option value="16">16</option>
        <option value="256">256</option>
        <option value="truecolor">truecolor</option>
      </select></label>
      <label><span>dither</span><select name="dither">
        <option value="none" selected>none</option>
        <option value="ordered">ordered</option>
        <option value="diffusion">diffusion</option>
        <option value="noise">noise</option>
      </select></label>
      <label><span>seed</span><input type="number" name="seed" step="1" placeholder="none"></label>
      <label><span>background</span><select name="background">
        <option value="auto" selected>auto (dark)</option>
        <option value="dark">dark</option>
        <option value="light">light</option>
      </select></label>
      <label class="check"><input type="checkbox" name="invert" value="on"><span>invert</span></label>
      <label class="check"><input type="checkbox" name="fg_only" value="on"><span>fg-only</span></label>
    </div>
  </fieldset>

  <fieldset>
    <legend>Alpha</legend>
    <div class="grid">
      <label><span>alpha</span><select name="alpha">
        <option value="transparent" selected>transparent</option>
        <option value="composite">composite</option>
      </select></label>
      <label><span>alpha bg</span><input type="text" name="alpha_bg" placeholder="#RRGGBB"></label>
      <label><span>alpha threshold</span><input type="number" name="alpha_threshold" value="128" min="0" max="255" step="1"></label>
    </div>
  </fieldset>

  <fieldset>
    <legend>Pre-processing</legend>
    <div class="grid">
      <label><span>brightness</span><input type="number" name="brightness" value="1.0" step="0.1" min="0" max="10"></label>
      <label><span>contrast</span><input type="number" name="contrast" value="1.0" step="0.1" min="0" max="10"></label>
      <label><span>gamma</span><input type="number" name="gamma" value="1.0" step="0.01" min="0.01" max="10"></label>
      <label><span>rotate</span><select name="rotate">
        <option value="0" selected>0</option>
        <option value="90">90</option>
        <option value="180">180</option>
        <option value="270">270</option>
      </select></label>
      <label class="check"><input type="checkbox" name="flip_x" value="on"><span>flip-x</span></label>
      <label class="check"><input type="checkbox" name="flip_y" value="on"><span>flip-y</span></label>
    </div>
  </fieldset>

  <fieldset>
    <legend>Output</legend>
    <div class="grid">
      <label><span>format</span><select name="format">
        <option value="text" selected>text</option>
        <option value="ansi">ansi</option>
        <option value="html">html</option>
      </select></label>
      <label class="check"><input type="checkbox" name="polite" value="on"><span>polite (strip unsafe escapes)</span></label>
    </div>
  </fieldset>

  <p><button type="submit" id="render-btn">Render</button></p>
</form>

<div id="status"></div>
<div id="error" class="error" hidden></div>
<div id="result-wrap" hidden>
  <p>
    <button type="button" id="copy-btn">Copy</button>
    <button type="button" id="download-btn">Download</button>
  </p>
  <pre id="result"></pre>
  <iframe id="result-frame" hidden title="rendered HTML output"></iframe>
</div>

<script>
(function () {
  "use strict";
  var form = document.getElementById("form");
  var fileInput = document.getElementById("file");
  var drop = document.getElementById("drop");
  var dropText = document.getElementById("drop-text");
  var preview = document.getElementById("preview");
  var statusEl = document.getElementById("status");
  var errorEl = document.getElementById("error");
  var resultWrap = document.getElementById("result-wrap");
  var resultPre = document.getElementById("result");
  var resultFrame = document.getElementById("result-frame");
  var copyBtn = document.getElementById("copy-btn");
  var downloadBtn = document.getElementById("download-btn");

  var file = null;
  var last = null;

  function formatBytes(n) {
    if (n < 1024) return n + " B";
    if (n < 1048576) return (n / 1024).toFixed(1) + " KiB";
    return (n / 1048576).toFixed(1) + " MiB";
  }

  function setFile(f) {
    file = f || null;
    if (!file) return;
    dropText.textContent = file.name + " (" + formatBytes(file.size) + ")";
    if (preview.src) URL.revokeObjectURL(preview.src);
    preview.src = URL.createObjectURL(file);
    preview.hidden = false;
  }

  function showError(msg) { errorEl.textContent = msg; errorEl.hidden = false; }
  function clearError() { errorEl.hidden = true; }

  drop.addEventListener("click", function () { fileInput.click(); });
  drop.addEventListener("keydown", function (e) {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); }
  });
  fileInput.addEventListener("change", function () { setFile(fileInput.files[0]); });
  ["dragenter", "dragover"].forEach(function (ev) {
    drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.add("dragover"); });
  });
  ["dragleave", "drop"].forEach(function (ev) {
    drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.remove("dragover"); });
  });
  drop.addEventListener("drop", function (e) {
    if (e.dataTransfer.files.length) setFile(e.dataTransfer.files[0]);
  });

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    clearError();
    resultWrap.hidden = true;
    if (!file) { showError("Choose or drop an image first."); return; }
    statusEl.textContent = "Rendering\u2026";
    var fd = new FormData(form);
    fd.set("image", file, file.name || "image");
    fetch("/render", { method: "POST", body: fd })
      .then(function (resp) {
        return resp.json().catch(function () {
          return { ok: false, error: "server returned a non-JSON response" };
        }).then(function (data) {
          if (!resp.ok || !data.ok) {
            showError(data.error || ("HTTP " + resp.status));
            statusEl.textContent = "";
            return;
          }
          last = data;
          statusEl.textContent = "";
          if (data.format === "html") {
            resultFrame.srcdoc = data.output;
            resultFrame.hidden = false;
            resultPre.hidden = true;
          } else {
            resultPre.textContent = data.preview;
            resultPre.hidden = false;
            resultFrame.hidden = true;
          }
          resultWrap.hidden = false;
        });
      })
      .catch(function (err) {
        showError("Request failed: " + err.message);
        statusEl.textContent = "";
      });
  });

  copyBtn.addEventListener("click", function () {
    if (!last) return;
    var done = function () {
      copyBtn.textContent = "Copied";
      setTimeout(function () { copyBtn.textContent = "Copy"; }, 1200);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(last.output).then(done, function () { fallbackCopy(last.output); done(); });
    } else {
      fallbackCopy(last.output); done();
    }
  });

  function fallbackCopy(text) {
    var ta = document.createElement("textarea");
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand("copy"); } catch (e) {}
    document.body.removeChild(ta);
  }

  downloadBtn.addEventListener("click", function () {
    if (!last) return;
    var blob = new Blob([last.output], { type: last.mime + ";charset=utf-8" });
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url;
    a.download = last.filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  });
})();
</script>
</body>
</html>
"""


if __name__ == "__main__":  # pragma: no cover
    main()
