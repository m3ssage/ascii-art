# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test, release, architecture, and sharp-edge notes that should travel with the code.

- Add durable project-specific notes here as they are discovered through real work.

## Build and test

- No system Python packages and no `pip` on `PATH`: use a project-local venv.
  `python3 -m venv .venv && .venv/bin/pip install -e . pytest`.
- Run everything with `.venv/bin/python -m pytest`. The parity suite skips unless
  incumbent converters are reachable; point it at them with
  `ASCII_ART_INCUMBENT_PATH=/path/to/bin`.
- After changing a rendering default, re-run `qual/make_baselines.py` and commit
  the updated `tests/baselines.json`, otherwise `tests/test_quality.py` fails by
  design.

## Architecture

- `src/ascii_art/render.py` owns the pipeline; `cli.py` only parses arguments,
  applies the pipe/exit-code contract, and prints. Keep it that way — the
  library is the product.
- One quantiser (`src/ascii_art/dither.py`) serves every mode: a grid of desired
  ink coverage in `[0, 1]` is mapped onto a sorted list of achievable targets.
  A new mode should express itself as (sub-samples per cell, targets) rather than
  as its own dithering code.
- The metric of report §7.1 lives in `src/ascii_art/quality.py` and is used by
  both the tests and `qual/`. Two entry points matter: `measure_output` (trims
  trailing blank lines, matching the report, for third-party tools) and
  `measure_canvas` (exact grid, for our own regression numbers). Trimming a
  dark-background render rescales the reference image and silently wrecks the
  score, which is why both exist.
- `src/ascii_art/web.py` is a browser front end over the renderer, not a second
  renderer: it parses a multipart form into `RenderOptions` (fields are named
  after the CLI flags), decodes via the library's own `load_image` stdin path,
  and returns `format_canvas` output. It is stdlib-only (`http.server`), never
  writes uploads to disk, and caps body size (`ASCII_ART_MAX_BODY_BYTES`) and
  decoded pixels (`ASCII_ART_MAX_PIXELS`). `Dockerfile`/`docker-compose.yml`
  package it; see the README's "Run the web app (Docker)" section.

## Sharp edges

- `ascii_art.quality` downscales with Lanczos on purpose: ImageMagick's default
  resize filter behaves like Lanczos, and the report's published incumbent
  numbers were produced with it. Switching to a box filter moved `RMSE_fit` by a
  factor of three on the same output.
- Braille blank cells are `U+2800`, never a plain space, so the grid survives
  whitespace-stripping pipelines.
- **A fixed absolute threshold is not a valid quantiser for an arbitrary
  image.** Content whose tonal band sits on one side of it collapses to
  all-or-nothing, and for a 1-bit mode that is a blank canvas. `dither.quantize`
  re-maps the valid range across the quantiser's range when (and only when) the
  direct pass returns a constant *extreme* level for non-uniform content;
  `_render_edges` does the same job for its peak-relative cutoff. Keep the
  recovery that narrow -- a constant canvas at an intermediate level is faithful
  (a checkerboard must stay uniform), and uniform input must never be stretched.
  The band sweep in `tests/test_render.py` is the guard.
- **`has_ink` in `canvas.py`, never `str.strip()`.** `U+2800 BRAILLE PATTERN
  BLANK` is not whitespace, so it survives a strip while drawing nothing, and
  `ink_cells()` would then report a blank braille canvas as full of ink. That
  false pass hid the braille collapse once already.
- **A colour depth must never change the glyph grid** — it may only add escapes.
  `_attach_cell_colors`/`_attach_two_colour_grids` set colour and nothing else;
  `--color 2` is the terminal background plus one forced ink colour, with tone
  from the mode's own quantiser. A depth with its own quantiser is how an empty
  canvas got shipped: thresholding luminance at mid-grey deletes every image
  whose tonal band sits on one side of 0.5. `tests/test_render.py` asserts the
  invariant across every mode and depth.
- `--background light` flips the ink axis, not the image. Terminal *background*
  colours for alpha compositing are `palette.TERMINAL_DARK`/`TERMINAL_LIGHT`;
  `INK_ON_DARK`/`INK_ON_LIGHT` are the ink colours for `--color 2`. Confusing
  the two is a one-line bug that only shows up on semi-transparent pixels.
- The verifier is `qual/RESULTS.md`, not a claim. Any new quality assertion must
  be reproducible with `qual/metric.py` and eyeballed once with
  `qual/figures.py`.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
