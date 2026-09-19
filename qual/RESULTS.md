# Quality results

This is the measured head-to-head the brief asked for: the report's section 7.1
metric, the report's own probe images, this implementation against every
incumbent the report tested.  It is the record of one run, not a promise — the
commands to reproduce it are at the bottom.

## What produced these numbers

`qual/metric.py`, which uses `ascii_art.quality`.  The report's published
incumbent numbers were produced with an ImageMagick-based script, so before
trusting any comparison the harness was re-run against the incumbents and
checked against the published table (report §3.5, `photo.png`):

| tool | report RMSE | here | report struct | here | report fit | here |
|---|---|---|---|---|---|---|
| `chafa-block` | 56.39 | 56.78 | 0.3286 | 0.3269 | 22.48 | 22.07 |
| `jp2a` | 120.86 | 119.87 | 0.2919 | 0.3118 | 20.03 | 21.08 |
| `ascii-image-converter` | 120.15 | 117.24 | 0.2329 | 0.2517 | 16.04 | 17.09 |
| `img2txt` | 123.33 | 122.18 | 1.3375 | 1.3451 | 69.24 | 68.39 |

Within ~7% on every axis, which is the agreement you get from a different
resize filter and grayscale weights.  `ascii_art.quality` downscales with
Lanczos because ImageMagick's default does; with a box filter the same
comparison was off by a factor of three on `RMSE_fit`, which is how that choice
was found.

## The four probes

Lower is better everywhere.  "Best incumbent" is the best score *any* incumbent
achieved on that image and axis.

### `photo.png` — a real photograph, 80 columns

| tool | grid | RMSE | RMSE_struct | RMSE_fit |
|---|---|---|---|---|
| **ascii-art block** | 80x40 | **24.64** | 0.3369 | 22.73 |
| **ascii-art braille --dither diffusion** | 80x40 | 96.50 | **0.1192** | **8.14** |
| **ascii-art ramp --dither diffusion** | 80x40 | 113.07 | 0.1476 | 10.08 |
| **ascii-art ramp** | 80x40 | 113.21 | 0.1501 | 10.24 |
| `ascii-image-converter -b --dither` | 80x40 | 114.29 | 0.2513 | 17.06 |
| `ascii-image-converter -W 80 -g` | 80x40 | 117.24 | 0.2517 | 17.09 |
| `jp2a --width=80` | 80x40 | 119.87 | 0.3118 | 21.08 |
| `chafa -c none` (blocks) | 80x40 | 56.78 | 0.3269 | 22.07 |
| `chafa --symbols ascii -c none` | 80x40 | 108.45 | 0.3304 | 22.30 |
| `img2txt -W 80` | 80x48 | 122.18 | 1.3451 | 68.39 |
| `ascii-art edges` | 80x40 | 155.26 | 1.5201 | 67.61 |

**Won on all three axes.** Structure is 2.1x better than the best incumbent
(0.1192 vs 0.2513) and the best-fit error is half (8.14 vs 17.06).  Raw RMSE is
the interesting one: `chafa`'s block symbols win raw RMSE among the incumbents
(56.78) because they can fill a whole cell, and our `block` mode beats that at
24.64 while staying monochrome.

`ascii-art edges` sitting near the bottom is expected and is exactly why the
design keeps `--mode edges` off by default: report §3.5 measured the same thing
for `jp2a --edge-threshold`.

### `shapes.png` — hard-edged flat art, 80 columns

| tool | grid | RMSE | RMSE_struct | RMSE_fit |
|---|---|---|---|---|
| **ascii-art block** | 80x30 | **6.36** | 0.0509 | 6.23 |
| **ascii-art ramp --dither diffusion** | 80x30 | 134.47 | **0.0362** | **4.43** |
| **ascii-art ramp** | 80x30 | 134.52 | 0.0375 | 4.59 |
| **ascii-art braille --dither diffusion** | 80x30 | 114.89 | 0.0380 | 4.64 |
| `ascii-image-converter -b` | 80x30 | 114.76 | 0.0498 | 6.09 |
| `chafa --symbols ascii -c none` | 80x30 | 134.06 | 0.0539 | 6.59 |
| `chafa -c none` (blocks) | 80x30 | 8.42 | 0.0657 | 8.03 |
| `jp2a --width=80` | 80x30 | 143.32 | 0.1083 | 13.22 |

**Won on all three axes.** `chafa` retains the best *structural* reputation on
diagrams, and it is 0.0657 against our 0.0362 here.

### `circle400.png` — a single smooth disc, 80 columns

| tool | grid | RMSE | RMSE_struct | RMSE_fit |
|---|---|---|---|---|
| `ascii-image-converter -b` | 80x40 | 93.30 | **0.0281** | **3.42** |
| **ascii-art braille** | 80x40 | 93.30 | 0.0327 | 3.98 |
| **ascii-art braille --dither diffusion** | 80x40 | 93.32 | 0.0331 | 4.04 |
| `chafa --symbols braille -c none` | 80x40 | 93.13 | 0.0403 | 4.92 |
| `chafa -c none` (blocks) | 80x35 | **73.32** | 0.5823 | 67.76 |
| `ascii-art ramp` | 80x35 | 113.21 | 0.5861 | 68.16 |
| `ascii-art block` | 80x34 | 82.51 | 0.6543 | 75.24 |

**Narrow loss.** `ascii-image-converter`'s braille edges us on structure
(0.0281 vs 0.0327) and fit (3.42 vs 3.98) — a 16% and 16% gap on a probe whose
content is one shape, where tiny threshold differences dominate.  Our braille
still beats `chafa`'s braille on both axes.

### `text.png` — legibility, 80 columns

| tool | grid | RMSE | RMSE_struct | RMSE_fit |
|---|---|---|---|---|
| `ascii-image-converter -b --dither` | 80x13 | 150.66 | **0.1832** | **3.21** |
| **ascii-art braille --dither diffusion** | 80x13 | 148.74 | 0.2675 | 4.66 |
| **ascii-art block** | 80x13 | **6.39** | 0.3482 | 6.03 |
| **ascii-art ramp --dither diffusion** | 80x13 | 173.46 | 0.3710 | 6.42 |
| **ascii-art ramp** | 80x13 | 173.45 | 0.3801 | 6.57 |
| `ascii-image-converter -W 80 -g` | 80x13 | 173.30 | 0.4656 | 7.97 |
| `chafa --symbols ascii -c none` | 80x14 | 172.09 | 0.9087 | 14.10 |
| `jp2a --width=80` | 80x13 | 184.27 | 0.9117 | 14.28 |
| `chafa -c none` (blocks) | 80x14 | 18.75 | 1.0000 | 17.42 |

**Mixed: one loss, one large win.**

* Loss: `ascii-image-converter -b --dither` leads on structure and fit, as the
  report said it would (its 2.74 `RMSE_fit` is the best number anywhere in that
  report).  Dither variants were measured against it on this probe and none
  closed the gap; plain per-dot error diffusion is kept because it wins overall:

  | braille dither variant | `text.png` RMSE_fit | `photo.png` RMSE_fit |
  |---|---|---|
  | `ascii-image-converter -b --dither` | 2.74 | 17.28 |
  | serpentine Floyd-Steinberg | 4.05 | 9.26 |
  | **per-dot Floyd-Steinberg (shipped)** | **4.12** | **8.23** |
  | cell-level 9-tone + diffusion | 4.80 | 19.37 |
  | cell-level 9-tone + ordered | 4.97 | 19.70 |
  | ordered Bayer 2x2 | 5.32 | 16.73 |
  | ordered Bayer 8x8 | 5.61 | 11.97 |
  | Atkinson | 5.58 | 10.03 |
  | ordered Bayer 4x4 | 5.71 | 11.32 |
  | seeded noise | 7.32 | - |

  Our `braille --dither diffusion` does beat that same tool on `photo.png`
  (0.1192 vs 0.2513, 8.14 vs 17.06), so this is a probe-specific gap rather
  than a general one.
* Win: our `block` mode scores **6.39 raw RMSE** against 18.75 for the best
  incumbent, a 3x improvement, because it can hold a full cell of ink and text
  is mostly background.  Our ramp also beats `ascii-image-converter`'s ramp
  (6.57 vs 7.97) and `jp2a`'s (14.28), which is the report's "ramp mode must
  reach ~6" bar.  Through the report's own ImageMagick metric the same ramp
  scores 6.26, and `ascii-image-converter`'s ramp scores 5.86 — the two
  measurements disagree by ~0.4, which is the size of the metric's own noise
  floor on this probe, so the honest statement is "level with the best
  incumbent ramp", not "better".

## Where the report's baselines are met

| report assertion | result |
|---|---|
| §6.3.1 structure must not regress versus a checked-in baseline | `tests/baselines.json`, enforced per case |
| §6.3.2 circle aspect within ±0.05 of 1.00 | 1.000 |
| §6.3.3 ramp legibility "~6" at 80 columns | 6.57 here, 6.26 with the report's metric |
| §6.3.4 ≥ 16 distinct glyphs in ramp mode | 61 on the 256-step ramp (jp2a managed 19) |
| §6.3.4 `--dither` improves `RMSE_struct` on the photo by ≥ 25% | 76.6% (braille: 0.6288 → 0.1469) |
| §6.3.5 alpha: ink only in the opaque third | 0 / 200 / 0 ink cells across the three thirds |
| §6.3.6 missing file ⇒ non-zero exit, empty stdout | exit 2, stdout empty, message on stderr |
| §6.3.6 `TERM_PROGRAM=ghostty` ⇒ output is still text | byte-identical to the scrubbed run |
| §6.3.6 a 3-frame GIF terminates | one frame, byte-identical to the still PNG |
| §6.3.6 two runs are byte-identical | identical, including seeded noise |
| §6.3.7 checkerboard must not alias | a single glyph across the whole grid |
| §6.3.8 12 MP source at 200x100 cells in the incumbent band | ~0.3 s (incumbents: 0.34-0.78 s) |

## Reproducing

The report's probe images lived in a scratch directory and were not committed, so
there are two paths.

Against this project's equivalent probes (no external tools needed):

```console
$ .venv/bin/python qual/make_fixtures.py
$ .venv/bin/python qual/metric.py --incumbents qual/images/photo.png qual/images/text.png
```

Against the report's original images, with the incumbents installed:

```console
$ ASCII_ART_INCUMBENT_PATH=/path/to/bin \
    .venv/bin/python qual/metric.py --incumbents photo.png shapes.png circle400.png text.png
```

And with the report's own script, verbatim in methodology, to check the metric
itself:

```console
$ PATH=/path/to/bin:$PATH python3 qual/metric_magick.py photo.png
```

Always look at a contact sheet before believing any of it:

```console
$ .venv/bin/python qual/figures.py --fixture photo
$ .venv/bin/python qual/figures.py --fixture text --out qual/out/text.png
```

That is not ceremony.  One of the bugs found while building this — braille
rendering `U+2800` versus a plain space — changes no metric by itself but
silently loses the bottom rows of every image that goes through a
whitespace-trimming pipeline, and it was found by rendering a circle and looking
at it.
