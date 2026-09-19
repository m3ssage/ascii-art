"""The CLI contract: exit codes, streams, pipe rules, determinism.

Every assertion here maps to a defect the report reproduced in an incumbent
(section 4) or a promise in section 6.1.
"""

from __future__ import annotations

import os
import pathlib
import pty
import subprocess
import sys

import pytest

pytestmark = pytest.mark.behaviour

ESC = "\x1b"

#: Every option the design's section 6.1 feature surface promises.
REQUIRED_OPTIONS = [
    "--formats",
    "--output",
    "--format",
    "--mode",
    "--chars",
    "--ramp",
    "--edge-threshold",
    "--color",
    "--dither",
    "--seed",
    "--background",
    "--invert",
    "--fg-only",
    "--alpha",
    "--alpha-bg",
    "--alpha-threshold",
    "--width",
    "--height",
    "--size",
    "--scale",
    "--fit",
    "--stretch",
    "--font-ratio",
    "--brightness",
    "--contrast",
    "--gamma",
    "--rotate",
    "--flip-x",
    "--flip-y",
    "--font",
    "--polite",
    "--help",
]

#: Escape sequences that would mean pixel-protocol output leaked through.
PIXEL_PROTOCOLS = ("\x1b_G", "\x1bPq", "\x1b]1337", "\x1b[?25l")


def _no_escapes(payload: bytes) -> bool:
    return ESC.encode() not in payload


# ---------------------------------------------------------------- exit codes


def test_missing_file_is_an_input_error_on_stderr_only(run, tmp_path):
    missing = tmp_path / "nope.png"
    result = run([str(missing)])
    assert result.returncode == 2
    assert result.stdout == b"", "an error must never write to stdout"
    assert result.stderr.strip(), "an error must be reported on stderr"
    assert str(missing) in result.err


def test_missing_file_does_not_corrupt_redirected_art(run, tmp_path):
    """The exact defect from report section 4.3 (ascii-image-converter)."""

    target = tmp_path / "art.txt"
    result = run([str(tmp_path / "nope.png"), "--output", str(target)])
    assert result.returncode == 2
    assert not target.exists() or target.read_text() == ""


def test_directory_input_is_an_input_error(run, tmp_path):
    result = run([str(tmp_path)])
    assert result.returncode == 2
    assert result.stdout == b""


def test_non_image_input_is_an_input_error(run, tmp_path):
    bogus = tmp_path / "bogus.png"
    bogus.write_text("this is not an image")
    result = run([str(bogus)])
    assert result.returncode == 2
    assert result.stdout == b""
    assert "recognised image format" in result.err


def test_empty_file_is_an_input_error(run, tmp_path):
    empty = tmp_path / "empty.png"
    empty.write_bytes(b"")
    result = run([str(empty)])
    assert result.returncode == 2
    assert result.stdout == b""


def test_unknown_option_is_a_usage_error(run):
    result = run(["--bogus"])
    assert result.returncode == 1
    assert result.stdout == b""


def test_bad_value_is_a_usage_error(run, paths):
    assert run([str(paths["photo"]), "--width", "abc"]).returncode == 1
    assert run([str(paths["photo"]), "--mode", "nope"]).returncode == 1
    assert run([str(paths["photo"]), "--color", "nope"]).returncode == 1
    assert run([str(paths["photo"]), "--dither", "nope"]).returncode == 1
    assert run([str(paths["photo"]), "--background", "nope"]).returncode == 1
    assert run([str(paths["photo"]), "--alpha", "nope"]).returncode == 1
    assert run([str(paths["photo"]), "--size", "nope"]).returncode == 1
    assert run([str(paths["photo"]), "--font-ratio", "nope"]).returncode == 1
    assert run([str(paths["photo"]), "--alpha-bg", "notacolour"]).returncode == 1
    assert run([str(paths["photo"]), "--scale", "nope"]).returncode == 1


def test_contradictory_options_are_usage_errors(run, paths):
    photo = str(paths["photo"])
    assert run([photo, "--size", "20x10", "--width", "20"]).returncode == 1
    assert run([photo, "--size", "20x10", "--fit", "--stretch"]).returncode == 1
    assert run([photo, "--format", "text", "--color", "truecolor"]).returncode == 1


def test_half_mode_is_refused_without_colour(run, paths):
    """Design 6.1: mono half-blocks are measurably broken (report 3.5)."""

    result = run([str(paths["photo"]), "--mode", "half"])
    assert result.returncode == 1
    assert result.stdout == b""
    assert "half" in result.err


def test_half_mode_is_refused_with_two_colours(run, paths):
    result = run([str(paths["photo"]), "--mode", "half", "--color", "2"])
    assert result.returncode == 1


def test_half_mode_works_with_colour(run, paths):
    result = run([str(paths["photo"]), "--mode", "half", "--color", "256", "--width", "20"])
    assert result.returncode == 0
    assert result.out.strip()


# --------------------------------------------------------------------- pipes


def test_pipe_gets_plain_text(run, paths):
    result = run([str(paths["photo"]), "--width", "30"])
    assert result.returncode == 0
    assert _no_escapes(result.stdout), "stdout is not a TTY, so output must be plain text"


def test_ghostty_environment_does_not_change_the_protocol(run, paths):
    """The report's worst defect (4.4): protocol chosen from environment markers."""

    clean = run([str(paths["photo"]), "--width", "30"])
    ghostty = run(
        [str(paths["photo"]), "--width", "30"],
        env={
            "TERM_PROGRAM": "ghostty",
            "GHOSTTY_BIN_DIR": "/usr/bin",
            "COLORTERM": "truecolor",
            "TERM": "xterm-256color",
        },
    )
    assert ghostty.returncode == 0
    assert _no_escapes(ghostty.stdout)
    assert ghostty.stdout == clean.stdout


def test_no_pixel_protocol_is_ever_emitted(run, paths):
    for args in (
        [str(paths["photo"]), "--width", "30"],
        [str(paths["photo"]), "--width", "30", "--format", "ansi", "--color", "truecolor"],
        [str(paths["photo"]), "--width", "30", "--format", "html"],
    ):
        out = run(args).out
        for marker in PIXEL_PROTOCOLS:
            assert marker not in out


def test_no_color_environment_disables_colour(run, paths):
    result = run([str(paths["photo"]), "--width", "30"], env={"NO_COLOR": "1"})
    assert _no_escapes(result.stdout)


def test_explicit_colour_overrides_no_color(run, paths):
    result = run(
        [str(paths["photo"]), "--width", "30", "--format", "ansi", "--color", "truecolor"],
        env={"NO_COLOR": "1"},
    )
    assert ESC.encode() in result.stdout


def test_output_file_defaults_to_plain_text(run, paths, tmp_path):
    target = tmp_path / "art.txt"
    result = run([str(paths["photo"]), "--width", "30", "--output", str(target)])
    assert result.returncode == 0
    assert result.stdout == b""
    payload = target.read_bytes()
    assert payload
    assert _no_escapes(payload)


# ------------------------------------------------------------------- formats


def test_format_text_is_plain_even_with_a_colour_terminal(run, paths):
    result = run([str(paths["photo"]), "--width", "20", "--format", "text"])
    assert _no_escapes(result.stdout)


def test_format_ansi_emits_sgr(run, paths):
    result = run(
        [str(paths["photo"]), "--width", "20", "--format", "ansi", "--color", "truecolor"]
    )
    assert ESC.encode() in result.stdout
    assert "\x1b[38;2;" in result.out


def test_format_html_is_a_standalone_document(run, paths):
    out = run([str(paths["photo"]), "--width", "20", "--format", "html"]).out
    assert out.startswith("<!DOCTYPE html>")
    assert "<pre" in out
    assert out.rstrip().endswith("</html>")
    # One line of text per rendered row: no accidental blank rows from the join.
    plain_rows = run([str(paths["photo"]), "--width", "20"]).out.splitlines()
    body = out.split('<pre class="ascii-art">', 1)[1].split("</pre>", 1)[0]
    assert body.count("\n") == len(plain_rows) + 1


def test_format_html_escapes_markup(run, paths):
    """Ramp glyphs include ``&``, ``<`` and ``>``; they must be escaped."""

    import re

    out = run([str(paths["photo"]), "--width", "60", "--format", "html"]).out
    body = out.split('<pre class="ascii-art">', 1)[1].split("</pre>", 1)[0]
    assert "<script" not in out
    assert re.search(r"&(?!#?[a-zA-Z0-9]+;)", body) is None, "raw markup survived into the html"
    assert "&amp;" in body or "&#x27;" in body, "glyphs needing escapes must be escaped"


def test_html_and_ansi_agree_on_the_glyphs(run, paths):
    html = run([str(paths["photo"]), "--width", "20", "--height", "5", "--format", "html"]).out
    plain = run([str(paths["photo"]), "--width", "20", "--height", "5"]).out
    import html as html_mod
    import re

    stripped = re.sub(r"<[^>]+>", "", html.split('<pre class="ascii-art">', 1)[1].split("</pre>", 1)[0])
    assert html_mod.unescape(stripped).strip("\n") == plain.strip("\n")


def test_fg_only_drops_background_escapes(run, paths):
    with_bg = run(
        [str(paths["photo"]), "--width", "20", "--format", "ansi", "--color", "256", "--mode", "block"]
    ).out
    fg_only = run(
        [
            str(paths["photo"]),
            "--width",
            "20",
            "--format",
            "ansi",
            "--color",
            "256",
            "--mode",
            "block",
            "--fg-only",
        ]
    ).out
    assert "\x1b[48;" in with_bg
    assert "\x1b[48;" not in fg_only
    assert len(fg_only) < len(with_bg)


def test_polite_output_has_no_cursor_control(run, paths):
    out = run(
        [str(paths["photo"]), "--width", "20", "--format", "ansi", "--color", "truecolor", "--polite"]
    ).out
    assert "\r" not in out
    for marker in PIXEL_PROTOCOLS:
        assert marker not in out


# ------------------------------------------------------------------- colour


def _strip_escapes(payload: bytes) -> bytes:
    import re

    return re.sub(rb"\x1b\[[0-9;]*m", b"", payload)


def test_two_colour_renders_on_every_background(run, paths):
    """Regression: `--color 2` drew an empty canvas on the default background.

    The reported file is a flat brand colour with no tonal range: every opaque
    pixel sat below mid-grey, so a fixed 0.5 threshold inked nothing at all.
    """

    for fixture in ("logo_dark", "logo_light"):
        for background in (None, "dark", "light"):
            args = [str(paths[fixture]), "--width", "80", "--color", "2"]
            if background:
                args += ["--background", background]
            result = run(args)
            assert result.returncode == 0, result.err
            assert _strip_escapes(result.stdout).strip(), (
                f"{fixture}: empty canvas with --background {background or 'omitted'}"
            )


def test_colour_depth_never_changes_the_glyphs(run, paths):
    """Raising the colour depth may add escapes; it must never add or drop art."""

    for fixture in ("logo_dark", "logo_light", "photo", "shapes"):
        plain = _strip_escapes(
            run([str(paths[fixture]), "--width", "60", "--color", "none"]).stdout
        )
        for depth in ("2", "8", "16", "256", "truecolor"):
            coloured = _strip_escapes(
                run([str(paths[fixture]), "--width", "60", "--color", depth]).stdout
            )
            assert coloured == plain, f"--color {depth} changed the art for {fixture}"


def test_two_colour_background_neighbours(run, paths):
    """`--color 2` with `--background` dark, light and omitted."""

    image = str(paths["logo_dark"])
    omitted = run([image, "--width", "80", "--color", "2"])
    explicit_dark = run([image, "--width", "80", "--color", "2", "--background", "dark"])
    automatic = run([image, "--width", "80", "--color", "2", "--background", "auto"])
    light = run([image, "--width", "80", "--color", "2", "--background", "light"])

    assert explicit_dark.returncode == light.returncode == 0
    assert explicit_dark.stdout == omitted.stdout
    assert automatic.stdout == omitted.stdout
    assert light.stdout != omitted.stdout, "--background light must flip the polarity"
    assert "\x1b[97m" in omitted.out, "dark background means white ink"
    assert "\x1b[30m" in light.out, "light background means black ink"
    # Both neighbours draw the same shape; only the glyphs' density differs.
    assert _strip_escapes(light.stdout).replace(b" ", b"") != b""
    assert len(_strip_escapes(light.stdout)) == len(_strip_escapes(omitted.stdout))


def test_removing_a_flag_restores_the_default(run, paths):
    """No flag may leak state: a default run is identical before and after."""

    image = str(paths["logo_light"])
    default = run([image, "--width", "60"]).stdout
    run(
        [
            image, "--width", "60", "--color", "2", "--background", "light",
            "--mode", "block", "--dither", "ordered", "--invert", "--seed", "3",
        ]
    )
    assert run([image, "--width", "60"]).stdout == default
    assert run([image, "--width", "60", "--color", "auto"]).stdout == default
    assert run([image, "--width", "60", "--background", "auto"]).stdout == default
    assert run([image, "--width", "60", "--dither", "none", "--seed", "0"]).stdout == default


def _dotted_cells(payload: bytes) -> int:
    """Count braille cells that actually have a dot lit."""

    text = _strip_escapes(payload).decode("utf-8", "replace")
    return sum(1 for ch in text if 0x2800 < ord(ch) <= 0x28FF)


def test_braille_renders_on_every_background(run, paths):
    """Regression: `--mode braille` drew no dotted cell at all.

    The reported file is a flat brand colour whose ink sits below braille's
    fixed 0.5 threshold, so on the default dark background every dot quantised
    to zero and the image rendered as nothing.
    """

    for fixture in ("logo_dark", "logo_light"):
        for background in (None, "dark", "light"):
            args = [str(paths[fixture]), "--width", "80", "--mode", "braille"]
            if background:
                args += ["--background", background]
            result = run(args)
            assert result.returncode == 0, result.err
            assert _dotted_cells(result.stdout) > 0, (
                f"{fixture}: no dotted cells with --background {background or 'omitted'}"
            )


def test_braille_neighbours_agree_with_the_default(run, paths):
    image = str(paths["logo_dark"])
    omitted = run([image, "--width", "80", "--mode", "braille"])
    assert run([image, "--width", "80", "--mode", "braille", "--background", "dark"]).stdout == omitted.stdout
    assert run([image, "--width", "80", "--mode", "braille", "--background", "auto"]).stdout == omitted.stdout
    assert run([image, "--width", "80", "--mode", "braille", "--background", "light"]).stdout != omitted.stdout


def test_every_mode_draws_something_for_a_low_contrast_logo(run, paths):
    """No render mode may return an empty canvas for content that exists."""

    for fixture in ("logo_dark", "logo_light"):
        for mode in ("ramp", "braille", "block", "edges"):
            for background in ("dark", "light"):
                result = run(
                    [
                        str(paths[fixture]), "--width", "80", "--mode", mode,
                        "--background", background,
                    ]
                )
                assert result.returncode == 0, result.err
                body = _strip_escapes(result.stdout).decode("utf-8", "replace")
                visible = body.replace(" ", "").replace("\u2800", "").strip()
                assert visible, f"{fixture} --mode {mode} --background {background} drew nothing"


def test_half_mode_draws_something_for_a_low_contrast_logo(run, paths):
    for fixture in ("logo_dark", "logo_light"):
        result = run([str(paths[fixture]), "--width", "80", "--mode", "half", "--color", "256"])
        assert result.returncode == 0, result.err
        body = _strip_escapes(result.stdout).decode("utf-8", "replace")
        assert body.replace(" ", "").strip()


# ------------------------------------------------------------------ interface


def test_help_is_complete(run):
    result = run(["--help"])
    assert result.returncode == 0
    for option in REQUIRED_OPTIONS:
        assert option in result.out, f"{option} missing from --help"
    assert "exit codes" in result.out
    assert "0 success" in result.out


def test_version(run):
    result = run(["--version"])
    assert result.returncode == 0
    assert "ascii-art" in result.out


def test_formats_lists_the_required_set(run):
    result = run(["--formats"])
    assert result.returncode == 0
    for name in ("PNG", "JPEG", "GIF", "WEBP", "BMP"):
        assert name in result.out


def test_stdin_dash_and_no_argument(run, paths):
    payload = paths["photo"].read_bytes()
    explicit = run(["-", "--width", "20"], stdin=payload)
    implicit = run(["--width", "20"], stdin=payload)
    assert explicit.returncode == 0
    assert explicit.stdout == implicit.stdout
    assert explicit.out.strip()


def test_multiple_files_are_all_rendered(run, paths):
    result = run([str(paths["photo"]), str(paths["shapes"]), "--width", "20"])
    assert result.returncode == 0
    single = run([str(paths["photo"]), "--width", "20"])
    assert result.stdout.count(b"\n") > single.stdout.count(b"\n")
    assert result.out.startswith(single.out.split("\n")[0])


# ----------------------------------------------------------------- behaviour


def test_animated_gif_terminates_on_the_first_frame(run, paths):
    """Report 4.7: ascii-image-converter never terminates on a 3-frame GIF."""

    result = run([str(paths["gif"]), "--width", "40"], timeout=30)
    assert result.returncode == 0
    lines = [line for line in result.out.splitlines() if line.strip()]
    assert 1 <= len(lines) <= 25, "one frame only, never an animated flood"

    from PIL import Image

    with Image.open(paths["gif"]) as handle:
        frame = handle.convert("RGB")
    still = paths["gif"].with_suffix(".first.png")
    frame.save(still)
    assert run([str(still), "--width", "40"]).stdout == result.stdout


def test_two_runs_are_byte_identical(run, paths):
    args = [str(paths["photo"]), "--width", "60", "--dither", "ordered"]
    assert run(args).stdout == run(args).stdout


def test_noise_dither_is_reproducible_with_a_seed(run, paths):
    args = [str(paths["photo"]), "--width", "60", "--dither", "noise", "--seed", "1234"]
    assert run(args).stdout == run(args).stdout
    other = run([str(paths["photo"]), "--width", "60", "--dither", "noise", "--seed", "99"])
    assert other.stdout != run(args).stdout


def test_noise_dither_without_a_seed_still_runs(run, paths):
    result = run([str(paths["photo"]), "--width", "30", "--dither", "noise"])
    assert result.returncode == 0


def test_exif_orientation_is_applied(run, paths):
    from PIL import Image

    tagged = paths["exif"]
    with Image.open(tagged) as handle:
        raw = handle.convert("RGB")
    assert raw.size == (60, 30)
    rotated = raw.transpose(Image.Transpose.ROTATE_270)
    expected = tagged.with_name("exif-rotated.png")
    rotated.save(expected)

    from_tag = run([str(tagged), "--width", "20"])
    from_plain = run([str(expected), "--width", "20"])
    assert from_tag.returncode == 0
    assert from_tag.stdout == from_plain.stdout
    assert from_tag.out.strip()


def test_broken_pipe_is_not_an_error(paths):
    """`ascii-art big.png | head -1` must not report failure."""

    script = (
        f"{sys.executable} -m ascii_art {paths['photo']} --width 90 | head -1 > /dev/null"
    )
    completed = subprocess.run(
        ["sh", "-o", "pipefail", "-c", script],
        capture_output=True,
        cwd=str(pathlib.Path(__file__).resolve().parent.parent),
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr.decode()


# ------------------------------------------------------------------------ TTY


def _run_with_pty(args, env=None):
    """Run the CLI with a real PTY on stdout so the TTY branch is exercised."""

    environ = dict(os.environ)
    for key in ("NO_COLOR", "TERM_PROGRAM", "GHOSTTY_BIN_DIR"):
        environ.pop(key, None)
    environ.update({"TERM": "xterm-256color", "COLORTERM": "truecolor"})
    if env:
        environ.update(env)
    master, slave = pty.openpty()
    try:
        process = subprocess.Popen(
            [sys.executable, "-m", "ascii_art", *args],
            stdin=subprocess.DEVNULL,
            stdout=slave,
            stderr=subprocess.PIPE,
            env=environ,
            cwd=str(pathlib.Path(__file__).resolve().parent.parent),
        )
        os.close(slave)
        chunks = []
        while True:
            try:
                chunk = os.read(master, 65536)
            except OSError:
                break
            if not chunk:
                break
            chunks.append(chunk)
        stderr = process.communicate(timeout=60)[1]
    finally:
        try:
            os.close(master)
        except OSError:
            pass
    return process.returncode, b"".join(chunks).replace(b"\r\n", b"\n"), stderr


def test_tty_gets_colour(paths):
    code, out, _ = _run_with_pty([str(paths["photo"]), "--width", "20"])
    assert code == 0
    assert ESC.encode() in out


def test_tty_with_no_color_is_plain(paths):
    code, out, _ = _run_with_pty([str(paths["photo"]), "--width", "20"], env={"NO_COLOR": "1"})
    assert code == 0
    assert _no_escapes(out)


def test_tty_with_format_text_is_plain(paths):
    code, out, _ = _run_with_pty([str(paths["photo"]), "--width", "20", "--format", "text"])
    assert code == 0
    assert _no_escapes(out)


def test_tty_terminal_width_is_detected(paths):
    code, out, _ = _run_with_pty(
        [str(paths["photo"]), "--format", "text"], env={"COLUMNS": "44", "LINES": "40"}
    )
    assert code == 0
    widths = {len(line) for line in out.decode().splitlines() if line.strip()}
    assert widths == {44}
