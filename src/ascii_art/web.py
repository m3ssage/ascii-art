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
  :root {
    --bg-0: #171030;
    --bg-1: #241542;
    --bg-2: #331a4f;
    --ink: #efe9ff;
    --ink-dim: rgba(239, 233, 255, .64);
    --pink: #ff5e8a;
    --amber: #ffd166;
    --panel: rgba(0, 0, 0, .35);
    --panel-line: rgba(255, 255, 255, .16);
    --stage: rgba(0, 0, 0, .45);
    --stage-line: rgba(255, 255, 255, .12);
  }
  *, *::before, *::after { box-sizing: border-box; }
  [hidden] { display: none !important; }
  html { min-height: 100%; }
  body {
    margin: 0;
    min-height: 100vh;
    background: linear-gradient(160deg, var(--bg-0) 0%, var(--bg-1) 55%, var(--bg-2) 100%);
    background-attachment: fixed;
    color: var(--ink);
    font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
    line-height: 1.45;
  }
  .wrap { max-width: 72rem; margin: 0 auto; padding: 1.2rem 1.4rem 3rem; }
  u { text-decoration-color: rgba(255, 94, 138, .6); }
  .sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }

  /* ---- top bar ---- */
  .topbar { display: flex; flex-wrap: wrap; align-items: center; gap: .8rem; margin-bottom: 1.1rem; }
  .logo { font-size: 1.9rem; font-weight: 900; letter-spacing: -.03em; line-height: 1; margin: 0; }
  .logo .dot { color: var(--pink); }
  .drop {
    margin-left: auto;
    display: flex; align-items: center; gap: .55rem;
    border: 2px dashed var(--panel-line);
    border-radius: .8rem;
    padding: .55rem .9rem;
    cursor: pointer;
    font-size: .82rem;
    color: var(--ink-dim);
    background: rgba(255, 255, 255, .03);
    max-width: 100%;
  }
  .drop.dragover { border-color: var(--pink); background: rgba(255, 94, 138, .12); }
  .drop img { max-width: 58px; max-height: 42px; border-radius: .4rem; display: block; }

  /* ---- mode tiles ---- */
  fieldset.mode-set { border: 0; padding: 0; margin: 0 0 .9rem; min-width: 0; }
  .mode-grid {
    display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: .6rem;
  }
  .mode-tile {
    position: relative;
    display: flex; flex-direction: column; align-items: center; gap: .15rem;
    padding: .7rem .4rem .6rem;
    text-align: center;
    background: rgba(255, 255, 255, .06);
    border: 1px solid rgba(255, 255, 255, .14);
    border-radius: .8rem;
    cursor: pointer;
    transition: border-color .12s ease, background .12s ease;
  }
  .mode-tile:hover { border-color: rgba(255, 255, 255, .4); }
  .mode-tile input { position: absolute; opacity: 0; pointer-events: none; }
  .mode-tile .glyph { font-family: ui-monospace, "DejaVu Sans Mono", "Noto Sans Mono", Menlo, Consolas, monospace; font-size: 1.05rem; line-height: 1; color: var(--amber); }
  .mode-tile .name { font-weight: 700; font-size: .82rem; }
  .mode-tile .desc { font-size: .66rem; opacity: .7; line-height: 1.15; }
  .mode-tile:has(input:checked) { border-color: var(--pink); background: rgba(255, 94, 138, .16); }
  .mode-tile:has(input:checked) .glyph { color: #fff; }
  .mode-tile:has(input:disabled) { opacity: .4; cursor: not-allowed; }

  /* ---- controls ---- */
  .controls {
    display: grid; grid-template-columns: repeat(12, minmax(0, 1fr)); gap: .75rem .85rem;
    background: rgba(0, 0, 0, .22); border: 1px solid rgba(255, 255, 255, .1);
    border-radius: 1rem; padding: .9rem .95rem; margin: 0 0 .9rem;
  }
  .ctl-label { display: flex; flex-direction: column; gap: .32rem; min-width: 0; }
  .ctl-label > span:first-child { font-size: .64rem; text-transform: uppercase; letter-spacing: .07em; opacity: .62; }
  .ctl { font: inherit; background: var(--panel); border: 1px solid var(--panel-line); border-radius: .6rem; color: var(--ink); padding: .45rem .55rem; width: 100%; }
  select.ctl { cursor: pointer; }
  input.ctl[type=number], input.ctl[type=text] { min-width: 0; }
  input[type=range] { width: 100%; accent-color: var(--pink); margin: 0; }
  .slider-row { display: flex; align-items: center; gap: .5rem; }
  .slider-row output { font-size: .72rem; font-variant-numeric: tabular-nums; opacity: .8; min-width: 2.4rem; text-align: right; }
  .span-3 { grid-column: span 3; }

  /* ---- advanced ---- */
  .advanced { margin: 0 0 .9rem; background: rgba(0, 0, 0, .22); border: 1px solid rgba(255, 255, 255, .1); border-radius: 1rem; }
  .advanced > summary { list-style: none; cursor: pointer; padding: .6rem .9rem; font-size: .8rem; color: var(--ink-dim); user-select: none; }
  .advanced > summary::-webkit-details-marker { display: none; }
  .advanced > summary::before { content: "▸ "; color: var(--pink); }
  .advanced[open] > summary::before { content: "▾ "; }
  .advanced .hint { opacity: .55; }
  .advanced-body { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: .75rem .85rem; padding: 0 .9rem .9rem; }
  .advanced-body .ctl-label, .advanced-body .check { grid-column: span 2; }
  .check { display: flex; align-items: center; gap: .45rem; font-size: .8rem; min-width: 0; }
  .check input { width: auto; accent-color: var(--pink); margin: 0; }
  .check span { min-width: 0; }

  /* ---- render button ---- */
  .render-row { display: flex; justify-content: flex-end; margin: 0 0 .9rem; }
  .btn { font: inherit; font-weight: 700; border-radius: .8rem; cursor: pointer; border: 1px solid transparent; padding: .6rem 1.15rem; }
  .btn.primary { background: var(--pink); color: #22103a; }
  .btn.primary:hover { filter: brightness(1.08); }
  .btn.ghost { background: rgba(255, 255, 255, .12); color: var(--ink); }
  .btn.ghost:hover { background: rgba(255, 255, 255, .2); }

  /* ---- stage ---- */
  .stage { background: var(--stage); border: 1px solid var(--stage-line); border-radius: .8rem; padding: .95rem; }
  .stage-meta { display: flex; flex-wrap: wrap; align-items: baseline; gap: .3rem 1.1rem; margin: 0 0 .55rem; font-size: .78rem; color: var(--ink-dim); }
  .stage-meta .file { color: var(--ink); }
  .stage-placeholder { color: var(--ink-dim); text-align: center; padding: 2.2rem 1rem; font-size: .9rem; }
  .stage-body { max-height: 70vh; overflow: auto; }
  pre.art { margin: 0; white-space: pre; font-family: ui-monospace, "DejaVu Sans Mono", "Noto Sans Mono", Menlo, Consolas, monospace; font-size: 12px; line-height: 1.12; letter-spacing: 0; color: inherit; }
  iframe#result-frame { width: 100%; height: 62vh; border: 0; background: #fff; border-radius: .5rem; }
  .stage-actions { display: flex; flex-wrap: wrap; gap: .5rem; margin-top: .75rem; }
  .stage-actions .hint { margin-left: auto; align-self: center; font-size: .72rem; color: var(--ink-dim); }

  #status { color: var(--ink-dim); min-height: 1.2em; margin-top: .5rem; font-size: .8rem; }
  .error { color: #ffb3c0; border: 1px solid var(--pink); border-radius: .6rem; padding: .5rem .8rem; margin: .5rem 0; white-space: pre-wrap; font-size: .85rem; }

  /* ---- bottom sheet (narrow) ---- */
  #sheet-toggle { display: none; }

  @media (max-width: 52rem) {
    .wrap { padding: .9rem .9rem 5.6rem; }
    .logo { font-size: 1.5rem; }
    .topbar { flex-direction: column; align-items: stretch; gap: .6rem; }
    .drop { margin-left: 0; width: 100%; justify-content: center; }

    /* tile carousel */
    .mode-grid { display: flex; overflow-x: auto; gap: .5rem; scroll-snap-type: x mandatory; padding-bottom: .25rem; -webkit-overflow-scrolling: touch; }
    .mode-tile { flex: 0 0 8.6rem; scroll-snap-align: start; }

    /* full-bleed stage */
    .stage { margin-left: -.9rem; margin-right: -.9rem; border-radius: 0; border-left: 0; border-right: 0; }

    /* controls collapse into a bottom sheet */
    #controls-sheet { position: fixed; left: 0; right: 0; bottom: 0; z-index: 20; max-height: 62vh; overflow-y: auto; transform: translateY(102%); visibility: hidden; transition: transform .25s ease, visibility .25s; background: rgba(20, 14, 38, .98); border-top: 1px solid var(--panel-line); border-radius: 1rem 1rem 0 0; padding: .9rem; }
    body.sheet-open #controls-sheet { transform: translateY(0); visibility: visible; }
    #controls-sheet .controls, #controls-sheet .advanced { margin-bottom: .6rem; border-radius: .8rem; }
    #controls-sheet .render-row { margin-bottom: 0; }
    #controls-sheet .render-row .btn { width: 100%; }
    #sheet-toggle {
      display: block;
      position: fixed; left: .75rem; right: .75rem; bottom: .75rem; z-index: 30;
      background: var(--pink); color: #22103a; border: 0; border-radius: .8rem;
      font: inherit; font-weight: 700; padding: .85rem 1rem; cursor: pointer;
      box-shadow: 0 8px 24px rgba(0, 0, 0, .4);
    }
    .controls { grid-template-columns: repeat(6, minmax(0, 1fr)); }
    .span-3 { grid-column: span 6; }
    .advanced-body { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .advanced-body .ctl-label, .advanced-body .check { grid-column: span 2; }
  }
</style>
</head>
<body>
<div class="wrap">
  <header class="topbar">
    <h1 class="logo">make something cool<span class="dot">.</span></h1>
    <label id="drop" class="drop" tabindex="0" role="button" aria-label="Choose or drop an image">
      <input type="file" id="file" accept="image/*" class="sr-only">
      <span id="drop-text">⬆ drop an image here — or <u>choose file</u> · png jpeg gif webp bmp</span>
      <img id="preview" alt="" hidden>
    </label>
  </header>

  <form id="form" autocomplete="off">
    <fieldset class="mode-set">
      <legend class="sr-only">render mode</legend>
      <div class="mode-grid">
        <label class="mode-tile">
          <input type="radio" name="mode" value="ramp" checked>
          <span class="glyph" aria-hidden="true">@%#*+=</span>
          <span class="name">ramp</span>
          <span class="desc">photos · the default</span>
        </label>
        <label class="mode-tile">
          <input type="radio" name="mode" value="braille">
          <span class="glyph" aria-hidden="true">⠿⠺⠖⠄</span>
          <span class="name">braille</span>
          <span class="desc">4× detail · square cells</span>
        </label>
        <label class="mode-tile">
          <input type="radio" name="mode" value="block">
          <span class="glyph" aria-hidden="true">█▄▀░</span>
          <span class="name">block</span>
          <span class="desc">best tone</span>
        </label>
        <label class="mode-tile">
          <input type="radio" name="mode" value="half">
          <span class="glyph" aria-hidden="true">▀▄▀▄</span>
          <span class="name">half</span>
          <span class="desc">colour only</span>
        </label>
        <label class="mode-tile">
          <input type="radio" name="mode" value="edges">
          <span class="glyph" aria-hidden="true">/|\-</span>
          <span class="name">edges</span>
          <span class="desc">line art</span>
        </label>
      </div>
    </fieldset>

    <div id="controls-sheet">
      <div class="controls">
        <label class="ctl-label span-3">
          <span>brightness</span>
          <span class="slider-row"><input type="range" id="brightness" name="brightness" min="0" max="2" step="0.05" value="1.0"><output for="brightness">1.0</output></span>
        </label>
        <label class="ctl-label span-3">
          <span>contrast</span>
          <span class="slider-row"><input type="range" id="contrast" name="contrast" min="0" max="2" step="0.05" value="1.0"><output for="contrast">1.0</output></span>
        </label>
        <label class="ctl-label span-3">
          <span>gamma</span>
          <span class="slider-row"><input type="range" id="gamma" name="gamma" min="0.2" max="3" step="0.05" value="1.0"><output for="gamma">1.0</output></span>
        </label>
        <label class="ctl-label span-3">
          <span>colour depth</span>
          <select class="ctl" name="color">
            <option value="auto" selected>auto</option>
            <option value="none">none</option>
            <option value="2">2</option>
            <option value="8">8</option>
            <option value="16">16</option>
            <option value="256">256</option>
            <option value="truecolor">truecolor</option>
          </select>
        </label>
        <label class="ctl-label span-3">
          <span>dither</span>
          <select class="ctl" name="dither">
            <option value="none" selected>none</option>
            <option value="ordered">ordered</option>
            <option value="diffusion">diffusion</option>
            <option value="noise">noise</option>
          </select>
        </label>
        <label class="ctl-label span-3">
          <span>background</span>
          <select class="ctl" name="background">
            <option value="auto" selected>auto</option>
            <option value="dark">dark</option>
            <option value="light">light</option>
          </select>
        </label>
        <label class="ctl-label span-3">
          <span>width (cells)</span>
          <input class="ctl" type="number" name="width" min="1" step="1" placeholder="80">
        </label>
      </div>

      <details class="advanced" id="advanced">
        <summary>advanced <span class="hint">chars · seed · ramp order · alpha · threshold · fit/stretch · font-ratio · rotate · flips · format</span></summary>
        <div class="advanced-body">
          <label class="ctl-label"><span>chars</span><input class="ctl" type="text" name="chars" placeholder="default measured ramp"></label>
          <label class="ctl-label"><span>seed</span><input class="ctl" type="number" name="seed" step="1" placeholder="none"></label>
          <label class="ctl-label"><span>ramp order</span><select class="ctl" name="ramp">
            <option value="measured" selected>measured</option>
            <option value="as-given">as-given</option>
          </select></label>
          <label class="ctl-label"><span>edge threshold</span><input class="ctl" type="number" name="edge_threshold" value="0.15" step="0.01" min="0"></label>
          <label class="ctl-label"><span>alpha</span><select class="ctl" name="alpha">
            <option value="transparent" selected>transparent</option>
            <option value="composite">composite</option>
          </select></label>
          <label class="ctl-label"><span>alpha bg</span><input class="ctl" type="text" name="alpha_bg" placeholder="#RRGGBB"></label>
          <label class="ctl-label"><span>alpha threshold</span><input class="ctl" type="number" name="alpha_threshold" value="128" min="0" max="255" step="1"></label>
          <label class="ctl-label"><span>height (cells)</span><input class="ctl" type="number" name="height" min="1" step="1" placeholder="auto"></label>
          <label class="ctl-label"><span>size (WxH)</span><input class="ctl" type="text" name="size" placeholder="80x40"></label>
          <label class="ctl-label"><span>scale (N|max)</span><input class="ctl" type="text" name="scale" placeholder="1"></label>
          <label class="ctl-label"><span>font ratio (W/H)</span><input class="ctl" type="text" name="font_ratio" value="1/2"></label>
          <label class="ctl-label"><span>rotate</span><select class="ctl" name="rotate">
            <option value="0" selected>0</option>
            <option value="90">90</option>
            <option value="180">180</option>
            <option value="270">270</option>
          </select></label>
          <label class="ctl-label"><span>format</span><select class="ctl" name="format" id="format">
            <option value="text" selected>text</option>
            <option value="ansi">ansi</option>
            <option value="html">html</option>
          </select></label>
          <label class="check"><input type="checkbox" name="fit" value="on"><span>fit (preserve aspect)</span></label>
          <label class="check"><input type="checkbox" name="stretch" value="on"><span>stretch (fill exactly)</span></label>
          <label class="check"><input type="checkbox" name="flip_x" value="on"><span>flip-x</span></label>
          <label class="check"><input type="checkbox" name="flip_y" value="on"><span>flip-y</span></label>
          <label class="check"><input type="checkbox" name="invert" value="on"><span>invert</span></label>
          <label class="check"><input type="checkbox" name="fg_only" value="on"><span>fg-only</span></label>
          <label class="check"><input type="checkbox" name="polite" value="on"><span>polite (strip unsafe escapes)</span></label>
        </div>
      </details>

      <div class="render-row">
        <button type="submit" class="btn primary" id="render-btn">Render ▸</button>
      </div>
    </div>

    <section class="stage" id="stage">
      <div class="stage-meta" id="stage-meta" hidden>
        <span class="file" id="stage-file"></span>
        <span id="stage-params"></span>
      </div>
      <div class="stage-placeholder" id="stage-placeholder">drop an image and hit render to see your art</div>
      <div class="stage-body" id="stage-body" hidden>
        <pre class="art" id="result"></pre>
        <iframe id="result-frame" hidden title="rendered HTML output"></iframe>
      </div>
      <div class="stage-actions" id="stage-actions" hidden>
        <button type="button" class="btn primary" id="copy-btn">Copy text</button>
        <button type="button" class="btn ghost" id="download-txt-btn">Download .txt</button>
        <button type="button" class="btn ghost" id="download-html-btn">Download .html</button>
        <span class="hint">transparent pixels stay transparent</span>
      </div>
    </section>

    <div id="status"></div>
    <div id="error" class="error" hidden></div>
  </form>
</div>

<button type="button" id="sheet-toggle">tune controls</button>

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
  var stagePlaceholder = document.getElementById("stage-placeholder");
  var stageMeta = document.getElementById("stage-meta");
  var stageFile = document.getElementById("stage-file");
  var stageParams = document.getElementById("stage-params");
  var stageBody = document.getElementById("stage-body");
  var stageActions = document.getElementById("stage-actions");
  var resultPre = document.getElementById("result");
  var resultFrame = document.getElementById("result-frame");
  var copyBtn = document.getElementById("copy-btn");
  var downloadTxtBtn = document.getElementById("download-txt-btn");
  var downloadHtmlBtn = document.getElementById("download-html-btn");
  var sheetToggle = document.getElementById("sheet-toggle");
  var colorSel = form.querySelector('select[name="color"]');
  var formatSel = form.querySelector('select[name="format"]');
  var halfRadio = form.querySelector('input[name="mode"][value="half"]');
  var rampRadio = form.querySelector('input[name="mode"][value="ramp"]');

  var file = null;
  var last = null;

  function formatBytes(n) {
    if (n < 1024) return n + " B";
    if (n < 1048576) return (n / 1024).toFixed(1) + " KiB";
    return (n / 1048576).toFixed(1) + " MiB";
  }

  function resetStage() {
    last = null;
    stagePlaceholder.hidden = false;
    stageMeta.hidden = true;
    stageBody.hidden = true;
    stageActions.hidden = true;
  }

  function setFile(f) {
    file = f || null;
    if (!file) return;
    resetStage();
    dropText.textContent = file.name + " (" + formatBytes(file.size) + ")";
    if (preview.src) URL.revokeObjectURL(preview.src);
    preview.src = URL.createObjectURL(file);
    preview.hidden = false;
  }

  function showError(msg) { errorEl.textContent = msg; errorEl.hidden = false; }
  function clearError() { errorEl.hidden = true; }

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

  function effectiveDepth() {
    if (colorSel.value !== "auto") return colorSel.value;
    return formatSel.value === "text" ? "none" : "truecolor";
  }

  function syncHalf() {
    var depth = effectiveDepth();
    var needsColour = depth === "none" || depth === "2";
    halfRadio.disabled = needsColour;
    if (needsColour && halfRadio.checked) rampRadio.checked = true;
  }
  colorSel.addEventListener("change", syncHalf);
  formatSel.addEventListener("change", syncHalf);
  syncHalf();

  form.querySelectorAll('input[type="range"]').forEach(function (r) {
    var out = r.parentElement.querySelector("output");
    r.addEventListener("input", function () { if (out) out.value = r.value; });
  });

  sheetToggle.addEventListener("click", function () {
    var open = document.body.classList.toggle("sheet-open");
    sheetToggle.textContent = open ? "close" : "tune controls";
  });

  function closeSheet() {
    document.body.classList.remove("sheet-open");
    sheetToggle.textContent = "tune controls";
  }

  function requestRender(format) {
    return new Promise(function (resolve, reject) {
      var fd = new FormData(form);
      fd.set("image", file, file.name || "image");
      if (format) fd.set("format", format);
      statusEl.textContent = "Rendering…";
      fetch("/render", { method: "POST", body: fd })
        .then(function (resp) {
          return resp.json().catch(function () {
            return { ok: false, error: "server returned a non-JSON response" };
          }).then(function (data) {
            if (!resp.ok || !data.ok) {
              statusEl.textContent = "";
              reject(new Error(data.error || ("HTTP " + resp.status)));
              return;
            }
            statusEl.textContent = "";
            resolve(data);
          });
        })
        .catch(reject);
    });
  }

  function describeParams() {
    var mode = form.querySelector('input[name="mode"]:checked');
    var dither = form.querySelector('select[name="dither"]').value;
    var background = form.querySelector('select[name="background"]').value;
    return [mode ? mode.value : "ramp", dither + " dither", background].join(" · ");
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    clearError();
    if (!file) { showError("Choose or drop an image first."); return; }
    requestRender(null).then(function (data) {
      last = data;
      stageFile.textContent = file.name;
      stageParams.textContent = describeParams();
      stagePlaceholder.hidden = true;
      stageMeta.hidden = false;
      stageBody.hidden = false;
      stageActions.hidden = false;
      if (data.format === "html") {
        resultFrame.srcdoc = data.output;
        resultFrame.hidden = false;
        resultPre.hidden = true;
      } else {
        resultPre.textContent = data.preview;
        resultPre.hidden = false;
        resultFrame.hidden = true;
      }
      closeSheet();
    }, function (err) {
      showError(err.message);
    });
  });

  function fallbackCopy(text) {
    var ta = document.createElement("textarea");
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand("copy"); } catch (e) {}
    document.body.removeChild(ta);
  }

  function copyText(text) {
    var done = function () {
      copyBtn.textContent = "Copied";
      setTimeout(function () { copyBtn.textContent = "Copy text"; }, 1200);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, function () { fallbackCopy(text); done(); });
    } else {
      fallbackCopy(text); done();
    }
  }

  copyBtn.addEventListener("click", function () {
    if (!last) return;
    copyText(last.preview);
  });

  function downloadBlob(data) {
    var blob = new Blob([data.output], { type: data.mime + ";charset=utf-8" });
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url;
    a.download = data.filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }

  function downloadAs(format) {
    if (!file) { showError("Choose or drop an image first."); return; }
    if (last && last.format === format) { downloadBlob(last); return; }
    requestRender(format).then(downloadBlob, function (err) { showError(err.message); });
  }

  downloadTxtBtn.addEventListener("click", function () { downloadAs("text"); });
  downloadHtmlBtn.addEventListener("click", function () { downloadAs("html"); });
})();
</script>
</body>
</html>
"""


if __name__ == "__main__":  # pragma: no cover
    main()
