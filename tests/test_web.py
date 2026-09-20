"""HTTP-layer tests for the web front end (``ascii_art.web``).

These exercise the real HTTP surface over a threaded server on an ephemeral
port: a render request returns a canvas, a bad upload returns a clear 400, an
over-size body is refused with 413, and invalid parameter combinations surface
readable messages.  No rendering or quality assertions live here — those are
the library's own suites; this file only checks the web shell.
"""

from __future__ import annotations

import http.client
import io
import json
import socket
import threading
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from typing import Dict, Optional, Tuple

import pytest

import fixtures
from ascii_art.web import make_server


def _png_bytes(size: int = 32) -> bytes:
    buf = io.BytesIO()
    fixtures.photo(size, size).save(buf, "PNG")
    return buf.getvalue()


def multipart_body(
    fields: Optional[Dict[str, str]] = None,
    *,
    image: Optional[bytes] = None,
    image_name: str = "image",
    filename: str = "image.png",
    image_content_type: str = "image/png",
    boundary: str = "----asciiArtTestBoundary42",
) -> Tuple[bytes, str]:
    """Build a ``multipart/form-data`` body by hand (no requests dependency)."""

    chunks: list = []
    for name, value in (fields or {}).items():
        chunks.append(
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
            ).encode("utf-8")
        )
        chunks.append(str(value).encode("utf-8"))
        chunks.append(b"\r\n")
    if image is not None:
        chunks.append(
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="{image_name}"; '
                f'filename="{filename}"\r\nContent-Type: {image_content_type}\r\n\r\n'
            ).encode("utf-8")
        )
        chunks.append(image)
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _request(
    port: int,
    method: str,
    path: str,
    *,
    body: Optional[bytes] = None,
    headers: Optional[Dict[str, str]] = None,
) -> Tuple[int, Dict[str, str], bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    conn.request(method, path, body=body, headers=headers or {})
    resp = conn.getresponse()
    data = resp.read()
    result = (resp.status, {k.lower(): v for k, v in resp.getheaders()}, data)
    conn.close()
    return result


@pytest.fixture(scope="module")
def server():
    """A live web server on an ephemeral port, torn down after the module."""

    srv = make_server(
        "127.0.0.1", 0, max_body_bytes=8 * 1024 * 1024, max_pixels=10_000_000
    )
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()
    thread.join(timeout=10)


def test_index_serves_the_browser_interface(server) -> None:
    status, _, body = _request(server.server_port, "GET", "/")
    assert status == 200
    html = body.decode("utf-8")
    assert "ascii-art" in html
    assert 'name="mode"' in html
    assert 'name="color"' in html


class _NumberInputs(HTMLParser):
    """Collect ``<input type="number">`` defaults from the served page."""

    def __init__(self) -> None:
        super().__init__()
        self.inputs: list = []

    def handle_starttag(self, tag, attrs) -> None:
        attributes = dict(attrs)
        if tag == "input" and attributes.get("type") == "number":
            self.inputs.append(attributes)


def _invalid_number_input(attrs: Dict[str, str]) -> Optional[str]:
    """Return why a browser rejects this control's default, if it does.

    This is the HTML5 constraint-validation rule for ``input[type=number]``:
    the default value must lie within ``min``/``max`` and sit on the ``step``
    grid.  The grid is anchored at ``min`` when one is given, which is exactly
    how the old ``gamma`` default escaped the ``0.1`` grid rooted at ``0.01``.
    """

    value = attrs.get("value")
    if value is None:
        return None
    name = attrs.get("name", "?")
    minimum = attrs.get("min")
    maximum = attrs.get("max")
    step = attrs.get("step")
    try:
        number = Decimal(value)
    except InvalidOperation:
        return f"{name}={value!r} is not a number"
    if minimum is not None and number < Decimal(minimum):
        return f"{name}={value!r} is below min={minimum}"
    if maximum is not None and number > Decimal(maximum):
        return f"{name}={value!r} is above max={maximum}"
    if step not in (None, "any"):
        base = Decimal(minimum) if minimum is not None else Decimal(0)
        steps = (number - base) / Decimal(step)
        if steps != steps.to_integral_value():
            return f"{name}={value!r} is off the step={step} grid based at {base}"
    return None


def test_default_form_fields_are_submittable(server) -> None:
    """The served form's defaults must pass browser constraint validation.

    A browser refuses to submit a form containing an invalid control, so every
    default in the served HTML has to satisfy its own ``min``/``max``/``step``.
    """

    status, _, body = _request(server.server_port, "GET", "/")
    assert status == 200
    parser = _NumberInputs()
    parser.feed(body.decode("utf-8"))
    problems = [
        problem
        for problem in map(_invalid_number_input, parser.inputs)
        if problem is not None
    ]
    assert not problems, "default form controls are invalid: " + "; ".join(problems)


def test_healthz(server) -> None:
    status, _, body = _request(server.server_port, "GET", "/healthz")
    assert status == 200
    assert body.strip() == b"ok"


def test_render_returns_a_canvas(server) -> None:
    body, content_type = multipart_body({"width": "40"}, image=_png_bytes())
    status, _, raw = _request(
        server.server_port,
        "POST",
        "/render",
        body=body,
        headers={"Content-Type": content_type},
    )
    assert status == 200
    data = json.loads(raw)
    assert data["ok"] is True
    assert data["format"] == "text"
    assert data["filename"] == "ascii-art.txt"
    assert len(data["output"]) > 0
    assert "\n" in data["output"]
    assert data["preview"].strip("\n")


def test_render_colour_ansi(server) -> None:
    body, content_type = multipart_body(
        {"width": "20", "format": "ansi", "color": "truecolor"}, image=_png_bytes()
    )
    status, _, raw = _request(
        server.server_port,
        "POST",
        "/render",
        body=body,
        headers={"Content-Type": content_type},
    )
    assert status == 200
    data = json.loads(raw)
    assert data["format"] == "ansi"
    assert "\x1b[" in data["output"]


def test_bad_upload_returns_clear_error(server) -> None:
    body, content_type = multipart_body({"width": "40"}, image=b"definitely not an image")
    status, _, raw = _request(
        server.server_port,
        "POST",
        "/render",
        body=body,
        headers={"Content-Type": content_type},
    )
    assert status == 400
    data = json.loads(raw)
    assert data["ok"] is False
    assert "not a recognised image format" in data["error"]


def test_missing_image_is_a_clear_error(server) -> None:
    body, content_type = multipart_body({"width": "40"})
    status, _, raw = _request(
        server.server_port,
        "POST",
        "/render",
        body=body,
        headers={"Content-Type": content_type},
    )
    assert status == 400
    data = json.loads(raw)
    assert "no image uploaded" in data["error"]


def test_wrong_content_type_is_refused(server) -> None:
    status, _, raw = _request(
        server.server_port,
        "POST",
        "/render",
        body=b"x",
        headers={"Content-Type": "application/json"},
    )
    assert status == 415
    data = json.loads(raw)
    assert "multipart/form-data" in data["error"]


def test_invalid_parameter_combination_is_clear(server) -> None:
    # ``half`` with no colour is the spec's named example of an invalid combo.
    body, content_type = multipart_body(
        {"mode": "half", "color": "none"}, image=_png_bytes()
    )
    status, _, raw = _request(
        server.server_port,
        "POST",
        "/render",
        body=body,
        headers={"Content-Type": content_type},
    )
    assert status == 400
    data = json.loads(raw)
    assert data["ok"] is False
    assert "half needs colour" in data["error"]


def test_over_size_body_is_refused_with_413() -> None:
    srv = make_server("127.0.0.1", 0, max_body_bytes=1024, max_pixels=100_000)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        sock = socket.create_connection(("127.0.0.1", srv.server_port), timeout=10)
        sock.sendall(
            b"POST /render HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Content-Type: multipart/form-data; boundary=x\r\n"
            b"Content-Length: 9999\r\n"
            b"\r\n"
        )
        chunks = []
        while True:
            part = sock.recv(4096)
            if not part:
                break
            chunks.append(part)
        sock.close()
        response = b"".join(chunks)
        assert b"413" in response
        assert b"too large" in response
        assert b"1024" in response
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=10)


def test_output_grid_cap_is_refused_with_413() -> None:
    srv = make_server("127.0.0.1", 0, max_pixels=10_000_000, max_cells=1000)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        body, content_type = multipart_body(
            {"width": "100", "height": "100", "stretch": "on"}, image=_png_bytes()
        )
        status, _, raw = _request(
            srv.server_port,
            "POST",
            "/render",
            body=body,
            headers={"Content-Type": content_type},
        )
        assert status == 413
        data = json.loads(raw)
        assert data["ok"] is False
        assert "ASCII_ART_MAX_CELLS" in data["error"]
        assert "1000" in data["error"]
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=10)


def test_auto_colour_depth_follows_the_output_format() -> None:
    from ascii_art.web import build_options

    _, _, depth_ansi, _, _ = build_options({"format": "ansi", "color": "auto"})
    _, _, depth_text, _, _ = build_options({"format": "text", "color": "auto"})
    assert depth_ansi == "truecolor"
    assert depth_text == "none"


def test_get_render_is_method_not_allowed(server) -> None:
    status, headers, raw = _request(server.server_port, "GET", "/render")
    assert status == 405
    assert headers.get("allow") == "POST"
    data = json.loads(raw)
    assert data["ok"] is False
