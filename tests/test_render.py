"""Library-level rendering behaviour.

These are the properties a caller of the importable API depends on, tested
without going through the CLI.
"""

from __future__ import annotations

from collections import Counter

import pytest
from PIL import Image

import fixtures

from ascii_art import RenderOptions, load_image, render, to_text
from ascii_art.dither import quantize
from ascii_art.errors import InputError, UsageError
from ascii_art.filters import FilterSpec, apply_filters
from ascii_art.geometry import parse_font_ratio, parse_size
from ascii_art.ramp import build_ramp, coverage_map
from ascii_art.canvas import has_ink
from ascii_art.render import _QUADRANT_GLYPHS

pytestmark = pytest.mark.render

BRAILLE_RANGE = range(0x2800, 0x2900)
EDGE_GLYPHS = {"-", "|", "/", "\\", " "}


def canvas_of(image, **kwargs):
    return render(image, RenderOptions(**kwargs), color_depth=kwargs.get("color", "none"))


# ----------------------------------------------------------------- downsampling


def test_checkerboard_renders_uniformly(images):
    """Report 3.4: an area average is the one hard requirement on downsampling."""

    canvas = canvas_of(images["checker"], width=40)
    glyphs = Counter(cell.glyph for row in canvas.cells for cell in row)
    assert set(glyphs) == {"Y"}, f"aliased downsampling: {glyphs}"


def test_checkerboard_with_an_odd_zoom_also_renders_uniformly(images):
    canvas = canvas_of(images["checker"], width=37)
    glyphs = Counter(cell.glyph for row in canvas.cells for cell in row)
    assert len(glyphs) == 1


def test_downsampling_averages_rather_than_samples():
    """Half black and half white must land on a middle ramp glyph, not either end."""

    image = Image.new("RGB", (2, 1))
    image.putpixel((0, 0), (0, 0, 0))
    image.putpixel((1, 0), (255, 255, 255))
    canvas = canvas_of(image, width=1)
    assert (canvas.cols, canvas.rows) == (1, 1)
    ramp = build_ramp()
    index = ramp.glyphs.index(canvas.cells[0][0].glyph)
    assert 0 < index < len(ramp.glyphs) - 1, "one of the two extremes was sampled, not averaged"
    assert abs(index / (len(ramp.glyphs) - 1) - 0.5) < 0.25


# -------------------------------------------------------------------- geometry


def _aspect(canvas):
    ink = canvas.ink_cells()
    assert ink, "nothing was drawn"
    xs = [p[0] for p in ink]
    ys = [p[1] for p in ink]
    width = max(xs) - min(xs) + 1
    height = max(ys) - min(ys) + 1
    return width / (2.0 * height)


def test_circle_stays_round(images):
    """Report 2.3 and test 6.3.2: apparent pixel aspect within +/-0.05 of 1.00."""

    canvas = canvas_of(images["circle"], width=80)
    assert canvas.rows == 40
    assert abs(_aspect(canvas) - 1.0) <= 0.05


@pytest.mark.parametrize("mode", ["ramp", "braille", "block", "edges"])
def test_circle_stays_round_in_every_mono_mode(images, mode):
    canvas = canvas_of(images["circle"], width=80, mode=mode)
    assert abs(_aspect(canvas) - 1.0) <= 0.05


def test_font_ratio_is_a_parameter_not_a_constant(images):
    """The correction must be adjustable, which is the point of --font-ratio."""

    square = images["circle"]
    default = canvas_of(square, width=80)
    stretched = canvas_of(square, width=80, font_ratio=1.0)
    assert default.rows == 40
    assert stretched.rows == 80


def test_geometry_options(images):
    image = images["circle"]
    assert canvas_of(image, width=60).cols == 60
    assert canvas_of(image, height=20).rows == 20
    assert (canvas_of(image, size="60x20").cols, canvas_of(image, size="60x20").rows) == (40, 20)
    assert canvas_of(image, size="60x20", stretch=True).cols == 60
    assert canvas_of(image, scale="2", width=30).cols == 60
    assert canvas_of(image, scale="max", term=(33, 44)).cols == 33


def test_geometry_helpers():
    assert parse_font_ratio("1/2") == 0.5
    assert parse_font_ratio("2:1") == 2.0
    assert parse_font_ratio("0.75") == 0.75
    assert parse_size("80x40") == (80, 40)
    assert parse_size("80x") == (80, None)
    assert parse_size("x40") == (None, 40)
    with pytest.raises(UsageError):
        parse_size("nope")
    with pytest.raises(UsageError):
        parse_font_ratio("0")


def test_piped_default_width_is_eighty(images):
    canvas = render(
        images["circle"], RenderOptions(is_tty=False), color_depth="none"
    )
    assert canvas.cols == 80


def test_tty_uses_the_terminal_width(images):
    canvas = render(
        images["circle"], RenderOptions(is_tty=True, term=(55, 40)), color_depth="none"
    )
    assert canvas.cols == 55


# ----------------------------------------------------------------------- alpha


def test_transparency_produces_ink_only_where_opaque(images):
    """Report 4.2: the one thing only chafa gets right."""

    canvas = canvas_of(images["alpha"], width=60)
    third = canvas.cols // 3
    counts = [
        sum(1 for y in range(canvas.rows) for x in range(lo, hi) if has_ink(canvas.cells[y][x].glyph))
        for lo, hi in ((0, third), (third, 2 * third), (2 * third, canvas.cols))
    ]
    assert counts[0] == 0, "transparent pixels must be no ink"
    assert counts[2] == 0, "transparent black must not become dense glyphs"
    assert counts[1] == canvas.rows * (2 * third - third)


def test_transparency_is_the_default(images):
    assert RenderOptions().alpha == "transparent"
    assert has_ink(canvas_of(images["alpha"], width=30).cells[0][0].glyph) is False


def test_alpha_composite_fills_the_whole_frame(images):
    canvas = canvas_of(
        images["alpha"], width=30, alpha="composite", alpha_bg=(255, 255, 255)
    )
    assert all(has_ink(canvas.cells[y][x].glyph) for y in range(canvas.rows) for x in range(canvas.cols))


def test_alpha_composite_fades_toward_the_declared_terminal_background():
    """Regression: the compositing colour is the terminal background, not the ink.

    A semi-transparent white pixel on a dark terminal must composite to mid grey,
    because the terminal paints black behind it -- not to white, which is what
    using the *ink* colour as the backdrop produced.
    """

    image = Image.new("RGBA", (40, 10), (255, 255, 255, 128))
    dark = canvas_of(image, width=20, alpha="composite", color="truecolor")
    white = canvas_of(
        image, width=20, alpha="composite", color="truecolor", alpha_bg=(255, 255, 255)
    )
    assert dark.cells[0][0].fg == (128, 128, 128)
    assert white.cells[0][0].fg == (255, 255, 255)


def test_alpha_bg_changes_the_composited_colour():
    image = Image.new("RGBA", (40, 10), (255, 0, 0, 128))
    dark = canvas_of(image, width=20, alpha="composite", color="truecolor")
    blue = canvas_of(
        image, width=20, alpha="composite", color="truecolor", alpha_bg=(0, 0, 255)
    )
    assert dark.cells[0][0].fg != blue.cells[0][0].fg


def test_alpha_threshold_is_honoured():
    image = Image.new("RGBA", (20, 20), (255, 255, 255, 100))
    assert has_ink(canvas_of(image, width=10, alpha_threshold=90).cells[0][0].glyph)
    assert not has_ink(canvas_of(image, width=10, alpha_threshold=200).cells[0][0].glyph)


# ---------------------------------------------------------------- background


def test_background_flips_the_ink_polarity():
    white = Image.new("RGB", (20, 20), (255, 255, 255))
    dark = canvas_of(white, width=10)
    light = canvas_of(white, width=10, background="light")
    assert has_ink(dark.cells[0][0].glyph), "bright image on a dark terminal needs dense ink"
    assert not has_ink(light.cells[0][0].glyph), "bright image on a light terminal needs no ink"


def test_background_auto_is_dark():
    from ascii_art.render import resolve_background

    assert resolve_background("auto") == "dark"


def test_invert_filter_flips_the_image():
    white = Image.new("RGB", (20, 20), (255, 255, 255))
    plain = canvas_of(white, width=10)
    inverted = canvas_of(white, width=10, filters=FilterSpec(invert=True))
    assert has_ink(plain.cells[0][0].glyph)
    assert not has_ink(inverted.cells[0][0].glyph)


# ----------------------------------------------------------------------- ramp


def test_measured_ramp_is_ordered_by_ink_coverage():
    ramp = build_ramp()
    coverage = coverage_map("".join(ramp.glyphs))
    values = [coverage[ch] for ch in ramp.glyphs]
    assert values == sorted(values), "the ramp must ascend in measured coverage"
    assert ramp.measured, "a monospace font is installed, so measurement must have run"


def test_measured_ordering_changes_a_reversed_glyph_set():
    chars = "@#*+=-:. "
    measured = build_ramp(chars, order="measured")
    as_given = build_ramp(chars, order="as-given")
    assert as_given.glyphs == tuple(chars)
    assert measured.glyphs[0] == " "
    assert measured.glyphs[-1] == "@"
    assert measured.glyphs != as_given.glyphs


def test_measured_ordering_beats_the_given_order(images):
    """Report 4.1: a measured ramp is the differentiator, so prove it is better."""

    from ascii_art.quality import measure_canvas

    chars = " .'`^\",:;Il!i><~+_-?][}{1)(|\\/tfjrxnuvczXYUJCLQ0OZmwqpdbkhao*#MW&8%B@$"
    results = {}
    for order in ("measured", "as-given"):
        canvas = render(
            images["photo"],
            RenderOptions(width=80, chars=chars, ramp_order=order),
            color_depth="none",
        )
        results[order] = measure_canvas(canvas, images["photo"])
    assert results["measured"].rmse_fit < results["as-given"].rmse_fit


def test_ramp_needs_two_glyphs():
    with pytest.raises(UsageError):
        build_ramp("x")


def test_chars_override_is_honoured():
    ramp = build_ramp(" .oO@", order="as-given")
    assert ramp.glyphs == (" ", ".", "o", "O", "@")
    assert ramp.levels == 5


# ----------------------------------------------------------------------- tone


def test_horizontal_gradient_is_monotonic_and_has_enough_levels(images):
    """Report 6.3.4: >= 16 distinct glyphs, and the probe must not be vertical."""

    canvas = canvas_of(images["ramp"], width=256)
    glyphs = {ch for y in range(canvas.rows) for ch in canvas.line(y) if ch.strip()}
    assert len(glyphs) >= 16, f"only {len(glyphs)} distinct glyphs"

    ramp = build_ramp()
    index = {ch: i for i, ch in enumerate(ramp.glyphs)}
    row = [index[ch] for ch in canvas.line(canvas.rows // 2)]
    assert row == sorted(row), "the gradient must be left-to-right and ascending"
    tenth = max(1, len(row) // 10)
    assert sum(row[:tenth]) / tenth < sum(row[-tenth:]) / tenth - 20


def test_vertical_gradient_is_constant_across_a_line():
    image = Image.new("RGB", (32, 256))
    for y in range(256):
        for x in range(32):
            image.putpixel((x, y), (y, y, y))
    canvas = canvas_of(image, width=32)
    first = canvas.line(0)
    assert len(set(first)) == 1
    assert canvas.line(0) != canvas.line(canvas.rows - 1)


# -------------------------------------------------------------------- dither


def test_dither_improves_braille_structure(images):
    """Report 4.8 / 6.3.4: the bar is a 25% improvement in RMSE_struct."""

    from ascii_art.quality import measure_canvas

    plain = measure_canvas(
        render(images["photo"], RenderOptions(width=80, mode="braille"), color_depth="none"),
        images["photo"],
    )
    dithered = measure_canvas(
        render(
            images["photo"],
            RenderOptions(width=80, mode="braille", dither="diffusion"),
            color_depth="none",
        ),
        images["photo"],
    )
    improvement = 1.0 - dithered.rmse_struct / plain.rmse_struct
    assert improvement >= 0.25, f"only {improvement:.1%} better"


@pytest.mark.parametrize("mode", ["ordered", "diffusion", "noise"])
def test_every_dither_mode_runs_and_changes_output(images, mode):
    from ascii_art.quality import measure_canvas

    base = measure_canvas(
        render(images["photo"], RenderOptions(width=40, mode="braille"), color_depth="none"),
        images["photo"],
    )
    variant = measure_canvas(
        render(
            images["photo"],
            RenderOptions(width=40, mode="braille", dither=mode, seed=1),
            color_depth="none",
        ),
        images["photo"],
    )
    assert (variant.rmse, variant.rmse_struct) != (base.rmse, base.rmse_struct)


def test_quantize_respects_the_valid_mask():
    values = [[0.9, 0.9], [0.9, 0.9]]
    valid = [[True, False], [True, False]]
    indices = quantize(values, (0.0, 1.0), "diffusion", None, valid)
    assert indices[0][1] == 0 and indices[1][1] == 0


def test_unknown_dither_is_rejected():
    with pytest.raises(UsageError):
        quantize([[0.5]], (0.0, 1.0), "nope")


# --------------------------------------------------------------------- modes


def test_braille_uses_braille_codepoints(images):
    canvas = canvas_of(images["photo"], width=40, mode="braille")
    used = {ord(ch) for row in canvas.cells for ch in (cell.glyph for cell in row) if ch != " "}
    assert used
    assert all(cp in BRAILLE_RANGE for cp in used)


def test_braille_blank_cells_keep_the_grid_shape(images):
    """A dotless braille cell is U+2800, so whitespace-trimming cannot eat rows."""

    canvas = canvas_of(images["circle"], width=40, mode="braille")
    assert set(canvas.line(0)) == {"\u2800"}, "blank rows must survive whitespace stripping"
    assert set(canvas.line(canvas.rows - 1)) == {"\u2800"}


def test_block_uses_quadrant_glyphs(images):
    canvas = canvas_of(images["shapes"], width=40, mode="block")
    allowed = set(_QUADRANT_GLYPHS.values())
    used = {cell.glyph for row in canvas.cells for cell in row}
    assert used <= allowed


def test_half_uses_half_blocks(images):
    canvas = canvas_of(images["photo"], width=40, mode="half", color="256")
    used = {cell.glyph for row in canvas.cells for cell in row}
    assert used <= {"\u2580", "\u2588", " "}
    assert "\u2580" in used, "two-tone cells must use the half block"


def test_edges_mode_uses_direction_glyphs(images):
    canvas = canvas_of(images["shapes"], width=40, mode="edges")
    used = {cell.glyph for row in canvas.cells for cell in row}
    assert used <= EDGE_GLYPHS
    assert used & {"-", "|", "/", "\\"}


def test_edges_threshold_controls_density(images):
    dense = canvas_of(images["shapes"], width=40, mode="edges", edge_threshold=0.02)
    sparse = canvas_of(images["shapes"], width=40, mode="edges", edge_threshold=0.9)
    assert len(dense.ink_cells()) > len(sparse.ink_cells())


def test_half_mode_needs_colour_at_library_level(images):
    with pytest.raises(UsageError):
        render(images["photo"], RenderOptions(mode="half", color="none"))


def test_unknown_mode_is_rejected(images):
    with pytest.raises(UsageError):
        render(images["photo"], RenderOptions(mode="nope"))


# -------------------------------------------------------------------- colour


def test_colour_none_has_no_colour(images):
    canvas = canvas_of(images["photo"], width=20, mode="block")
    assert all(cell.fg is None and cell.bg is None for row in canvas.cells for cell in row)


def test_colour_flags_never_change_the_glyph_grid(images):
    """A colour depth may only add escapes; it must never add or drop glyphs.

    This is the invariant that the empty-canvas bug violated: ``--color 2`` used
    its own quantiser, so raising the colour depth silently deleted art.  It
    holds for every mode and every depth.
    """

    for fixture in ("photo", "logo_dark", "logo_light", "shapes", "alpha"):
        image = images[fixture]
        for mode in ("ramp", "braille", "block", "edges"):
            reference = None
            for depth in ("none", "2", "8", "16", "256", "truecolor"):
                canvas = render(
                    image, RenderOptions(width=60, mode=mode), color_depth=depth
                )
                grid = tuple(canvas.line(y) for y in range(canvas.rows))
                if reference is None:
                    reference = grid
                    assert any(line.strip() for line in grid), (
                        f"{fixture}/{mode} with --color none produced no ink at all"
                    )
                else:
                    assert grid == reference, f"{fixture}/{mode}: --color {depth} changed the glyphs"


def test_two_colour_never_empties_a_low_contrast_logo(images):
    """Regression: a flat mark on black used to render as an empty canvas.

    Both fixtures are opaque and both have a tonal band entirely on one side of
    mid-grey, which is the condition that broke a fixed 0.5 threshold.
    """

    for fixture in ("logo_dark", "logo_light"):
        for background in ("dark", "light"):
            canvas = render(
                images[fixture],
                RenderOptions(width=80, background=background),
                color_depth="2",
            )
            assert canvas.ink_cells(), f"{fixture} with --background {background} drew nothing"


def test_two_colour_has_exactly_two_colours(images):
    canvas = render(images["logo_dark"], RenderOptions(width=40), color_depth="2")
    inks = {cell.fg for row in canvas.cells for cell in row if cell.fg}
    assert inks <= {(255, 255, 255), (0, 0, 0)}
    assert inks == {(255, 255, 255)}
    dark_bg = render(
        images["logo_dark"], RenderOptions(width=40, background="light"), color_depth="2"
    )
    assert {cell.fg for row in dark_bg.cells for cell in row if cell.fg} == {(0, 0, 0)}


def test_two_colour_polarity_follows_the_background():
    """Brighter content takes the ink on a dark terminal, darker on a light one."""

    image = Image.new("RGB", (40, 20), (0, 0, 0))
    for x in range(20, 40):
        for y in range(20):
            image.putpixel((x, y), (170, 170, 170))

    ramp = build_ramp()
    order = {ch: i for i, ch in enumerate(ramp.glyphs)}

    def half_ink(canvas, left):
        span = range(0, canvas.cols // 2) if left else range(canvas.cols // 2, canvas.cols)
        return sum(order.get(canvas.cells[y][x].glyph, 0) for y in range(canvas.rows) for x in span)

    dark = render(image, RenderOptions(width=20), color_depth="2")
    assert half_ink(dark, left=False) > half_ink(dark, left=True), "bright half must be inked"

    light = render(image, RenderOptions(width=20, background="light"), color_depth="2")
    assert half_ink(light, left=True) > half_ink(light, left=False), "dark half must be inked"


def test_two_colour_renders_in_every_mode(images):
    """A tonal band that never approaches mid-grey still renders, in every mode."""

    for mode in ("ramp", "braille", "block", "edges"):
        canvas = render(images["logo_dark"], RenderOptions(width=80, mode=mode), color_depth="2")
        assert canvas.ink_cells(), f"--mode {mode} --color 2 drew nothing"


def test_quantize_recovers_only_from_all_or_nothing():
    """The degenerate-output rescue has to be narrow enough to be safe.

    A nearly-flat mid-grey grid must be left exactly as it quantised, or an
    anti-aliased checkerboard would be stretched into a black-and-white pattern.
    """

    targets = (0.0, 0.5, 1.0)

    flat_mid = [[0.499, 0.501], [0.500, 0.499]]
    assert quantize(flat_mid, targets) == quantize(
        flat_mid, targets, recover_degenerate=False
    )

    dark = [[0.01, 0.03], [0.02, 0.04]]
    assert {i for row in quantize(dark, targets, recover_degenerate=False) for i in row} == {0}
    assert len({i for row in quantize(dark, targets) for i in row}) > 1

    uniform = [[0.02, 0.02], [0.02, 0.02]]
    assert {i for row in quantize(uniform, targets) for i in row} == {0}


@pytest.mark.parametrize("mode", ["ramp", "braille", "block", "edges"])
@pytest.mark.parametrize("background", ["dark", "light"])
@pytest.mark.parametrize("band", fixtures.TONE_BANDS)
def test_no_mode_draws_an_empty_canvas_for_non_uniform_content(mode, background, band):
    """The guarantee: content that exists must be drawn, whatever its tone.

    A fixed absolute threshold collapses any image whose tonal band sits wholly
    on one side of it.  Braille on a transparent-background logo was the
    reported symptom; all-or-nothing is the tested property.
    """

    image = fixtures.tonal_band(*band)
    canvas = render(
        image, RenderOptions(width=60, mode=mode, background=background), color_depth="none"
    )
    assert canvas.ink_cells(), f"--mode {mode} --background {background} on band {band} drew nothing"


def test_transparent_plate_low_contrast_logo_renders_in_every_mode(images):
    """The reported file's shape: content only where alpha is set."""

    for fixture in ("logo_dark", "logo_light"):
        for mode in ("ramp", "braille", "block", "edges"):
            for background in ("dark", "light"):
                canvas = render(
                    images[fixture],
                    RenderOptions(width=80, mode=mode, background=background),
                    color_depth="none",
                )
                assert canvas.ink_cells(), (
                    f"{fixture} --mode {mode} --background {background} drew nothing"
                )


def test_edges_mode_falls_back_when_alpha_hides_every_edge():
    """A cutoff relative to the global peak can exclude every *valid* gradient."""

    image = fixtures.tonal_band(0.60, 0.95, transparent=True)
    canvas = render(
        image, RenderOptions(width=60, mode="edges", background="light"), color_depth="none"
    )
    assert canvas.ink_cells()


def test_edges_mode_still_draws_nothing_for_flat_content():
    """The fallback must not invent edges where there is no content.

    Only the interior is asserted.  ``_render_edges`` samples outside the image
    as 0.0, so the outermost row and column read as a step edge and have always
    been drawn; that is unrelated to the collapse recovery and is left alone.
    """

    flat = Image.new("RGB", (60, 30), (128, 128, 128))
    canvas = render(flat, RenderOptions(width=30, mode="edges"), color_depth="none")
    interior = [
        (x, y)
        for y in range(1, canvas.rows - 1)
        for x in range(1, canvas.cols - 1)
        if has_ink(canvas.cells[y][x].glyph)
    ]
    assert not interior


def test_truecolor_keeps_exact_colours(images):
    canvas = canvas_of(images["photo"], width=20, color="truecolor")
    colours = {cell.fg for row in canvas.cells for cell in row if cell.fg}
    assert len(colours) > 8


def test_palette_depths_snap_to_the_palette(images):
    from ascii_art.palette import PALETTE_16, palette_256

    sixteen = canvas_of(images["photo"], width=20, color="16")
    assert {cell.fg for row in sixteen.cells for cell in row if cell.fg} <= set(PALETTE_16)
    deep = canvas_of(images["photo"], width=20, color="256")
    assert {cell.fg for row in deep.cells for cell in row if cell.fg} <= set(palette_256())


def test_resolve_depth_is_conservative_off_a_tty():
    from ascii_art.palette import resolve_depth

    assert resolve_depth("auto", is_tty=False, fmt="ansi", env={}) == "none"
    assert resolve_depth("auto", is_tty=False, fmt="ansi", env={"NO_COLOR": "1"}) == "none"
    assert (
        resolve_depth("auto", is_tty=True, fmt="ansi", env={"COLORTERM": "truecolor"})
        == "truecolor"
    )
    assert resolve_depth("auto", is_tty=True, fmt="ansi", env={"TERM": "xterm-256color"}) == "256"
    assert resolve_depth("auto", is_tty=True, fmt="ansi", env={"TERM": "dumb"}) == "none"
    assert resolve_depth("truecolor", is_tty=False, fmt="ansi", env={}) == "truecolor"
    assert resolve_depth("auto", is_tty=False, fmt="html", env={}) == "truecolor"


# ------------------------------------------------------------------- filters


def test_identity_filters_do_not_copy_the_image(images):
    spec = FilterSpec()
    assert spec.is_identity
    image = images["photo"]
    assert apply_filters(image, spec) is image


def test_brightness_raises_luminance():
    grey = Image.new("RGBA", (10, 10), (100, 100, 100, 255))
    brighter = apply_filters(grey, FilterSpec(brightness=1.5))
    assert brighter.convert("L").getpixel((0, 0)) > 100


def test_contrast_and_gamma_change_the_image():
    pair = Image.new("RGBA", (2, 1))
    pair.putpixel((0, 0), (60, 60, 60, 255))
    pair.putpixel((1, 0), (100, 100, 100, 255))

    def spread(image):
        grey = image.convert("L")
        return grey.getpixel((1, 0)) - grey.getpixel((0, 0))

    assert spread(apply_filters(pair, FilterSpec(contrast=2.0))) > spread(pair)
    brighter = apply_filters(pair, FilterSpec(gamma=2.0)).convert("L").getpixel((0, 0))
    darker = apply_filters(pair, FilterSpec(gamma=0.5)).convert("L").getpixel((0, 0))
    assert brighter > 60 > darker


def test_rotate_and_flip_dimensions(images):
    image = Image.new("RGBA", (40, 20), (255, 255, 255, 255))
    assert apply_filters(image, FilterSpec(rotate=90)).size == (20, 40)
    assert apply_filters(image, FilterSpec(rotate=180)).size == (40, 20)
    assert apply_filters(image, FilterSpec(flip_x=True)).size == (40, 20)


def test_alpha_survives_colour_filters():
    image = Image.new("RGBA", (4, 4), (10, 20, 30, 77))
    out = apply_filters(image, FilterSpec(brightness=2.0))
    assert out.getchannel("A").getpixel((0, 0)) == 77


def test_filter_validation_rejects_out_of_range():
    from ascii_art.filters import validate

    with pytest.raises(UsageError):
        validate(FilterSpec(brightness=-1.0))
    with pytest.raises(UsageError):
        validate(FilterSpec(rotate=45))


# -------------------------------------------------------------------- loader


def test_loader_reports_missing_files(tmp_path):
    with pytest.raises(InputError):
        load_image(str(tmp_path / "nope.png"))


def test_loader_reads_every_required_format(tmp_path, images):
    photo = images["photo"].convert("RGB")
    for suffix, fmt in (
        (".png", "PNG"),
        (".jpg", "JPEG"),
        (".bmp", "BMP"),
        (".webp", "WEBP"),
    ):
        target = tmp_path / f"image{suffix}"
        photo.save(target, format=fmt)
        loaded = load_image(str(target))
        assert loaded.size == photo.size
        assert loaded.mode == "RGBA"


def test_loader_reads_the_first_gif_frame(tmp_path, images):
    import fixtures

    target = fixtures.tricolor_gif(tmp_path / "tri.gif")
    loaded = load_image(str(target))
    assert loaded.size == (160, 160)
    assert loaded.getpixel((5, 5))[:3] == (220, 30, 30)


def test_formats_report_lists_the_core_set():
    from ascii_art.loader import available_formats, formats_report

    core, _ = available_formats()
    assert set(core) >= {"PNG", "JPEG", "GIF", "WEBP", "BMP"}
    assert "PNG" in formats_report()


# -------------------------------------------------------------------- output


def test_text_output_is_rectangular(images):
    canvas = canvas_of(images["photo"], width=30)
    lines = to_text(canvas).splitlines()
    assert len(lines) == canvas.rows
    assert {len(line) for line in lines} == {canvas.cols}


def test_ansi_output_never_leaks_cursor_control(images):
    from ascii_art.output import strip_unsafe_escapes, to_ansi

    canvas = canvas_of(images["photo"], width=20, color="truecolor")
    text = to_ansi(canvas, "truecolor", polite=True)
    assert strip_unsafe_escapes(text) == text


def test_strip_unsafe_escapes_removes_osc_and_csi():
    from ascii_art.output import strip_unsafe_escapes

    dirty = "\x1b]0;title\x07\x1b[2J\x1b[?25l\x1b[31mred\x1b[0m\r"
    assert strip_unsafe_escapes(dirty) == "\x1b[31mred\x1b[0m"


def test_html_has_one_line_per_row(images):
    from ascii_art.output import to_html

    canvas = canvas_of(images["photo"], width=12, color="truecolor")
    body = to_html(canvas, "truecolor").split('<pre class="ascii-art">', 1)[1].split("</pre>", 1)[0]
    assert body.strip("\n").count("\n") + 1 == canvas.rows
