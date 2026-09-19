#!/usr/bin/env python3
"""The report's section 7.1 metric, verbatim in methodology.

This is the report's own script with one change: our implementation is added to
the ``TESTS`` table.  It exists so the cross-check recorded in ``qual/RESULTS.md``
is reproducible -- the report's published incumbent numbers were produced with
*this* method (ImageMagick resize, which behaves like Lanczos, and a font
advance measured through ``magick label:``), so comparing against them with a
different downsampling filter would not be apples to apples.

It needs ImageMagick and the incumbent tools on ``PATH``::

    PATH=/path/to/nixprofile/bin:$PATH \\
        python3 qual/metric_magick.py qual/images/photo.png

The project's own metric (``ascii_art.quality``) agrees with this one to within
about 0.1-0.5 RMSE; ``tests/`` and ``qual/metric.py`` use that one because it
needs nothing but Pillow.
"""

import os
import re
import subprocess
import sys

LAB = os.environ.get("ASCII_ART_LAB", os.getcwd())
FONT = os.environ.get(
    "ASCII_ART_FONT", "/usr/share/fonts/TTF/JetBrainsMonoNerdFont-Regular.ttf"
)
OURS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".venv", "bin", "ascii-art")


def _advance(pointsize):
    r = subprocess.run(
        ["magick", "-font", FONT, "-pointsize", str(pointsize), "label:" + "M" * 10,
         "-format", "%w", "info:"],
        capture_output=True, text=True,
    )
    return float(r.stdout) / 10.0


CW = 16
POINTSIZE = round(CW / (_advance(100) / 100.0), 2)
CH = 2 * CW
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")


def tool_output(cmd, img):
    r = subprocess.run(cmd + [img], capture_output=True, text=True, timeout=180, cwd=LAB)
    return ANSI.sub("", r.stdout)


def grid(text):
    lines = text.split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        return []
    w = max(len(line) for line in lines)
    return [line.ljust(w) for line in lines]


def rasterise(lines, path):
    h, w = len(lines), len(lines[0])
    mvg = [
        f"viewbox 0 0 {w * CW} {h * CH}",
        f"fill black rectangle 0,0 {w * CW},{h * CH}",
        f'fill white font "{FONT}" font-size {POINTSIZE}',
    ]
    for r, line in enumerate(lines):
        for c, ch in enumerate(line):
            if ch in " \t":
                continue
            e = ch.replace("\\", "\\\\").replace("'", "\\'")
            mvg.append(f"text {c * CW},{r * CH + int(CH * 0.80)} '{e}'")
    p = os.path.join(LAB, "qual", "draw.mvg")
    open(p, "w").write("\n".join(mvg))
    subprocess.run(
        ["magick", "-size", f"{w * CW}x{h * CH}", "xc:black", "-draw", "@" + p, path],
        check=True, capture_output=True, cwd=LAB,
    )


def lum_grid(src, out, w, h):
    pgm = os.path.join(LAB, "qual", out + ".pgm")
    subprocess.run(
        ["magick", src, "-colorspace", "Gray", "-resize", f"{w}x{h}!", "-depth", "8", pgm],
        check=True, capture_output=True, cwd=LAB,
    )
    return list(
        subprocess.run(
            ["magick", pgm, "-depth", "8", "gray:-"], capture_output=True, check=True, cwd=LAB
        ).stdout
    )


def stats(got, ref):
    n = min(len(got), len(ref))
    got, ref = got[:n], ref[:n]
    rmse = (sum((got[i] - ref[i]) ** 2 for i in range(n)) / n) ** 0.5
    mg, mr = sum(got) / n, sum(ref) / n
    sg = (sum((x - mg) ** 2 for x in got) / n) ** 0.5 or 1
    sr = (sum((x - mr) ** 2 for x in ref) / n) ** 0.5 or 1
    rs = (sum(((got[i] - mg) / sg - (ref[i] - mr) / sr) ** 2 for i in range(n)) / n) ** 0.5
    den = sum((g - mg) ** 2 for g in got) or 1
    a = sum((got[i] - mg) * (ref[i] - mr) for i in range(n)) / den
    b = mr - a * mg
    rf = (sum((a * got[i] + b - ref[i]) ** 2 for i in range(n)) / n) ** 0.5
    return rmse, rs, rf, mg, mr


def compare(name, cmd, img):
    lines = grid(tool_output(cmd, img))
    if not lines:
        return dict(tool=name, error="no output / tool failed")
    h, w = len(lines), len(lines[0])
    png = os.path.join(LAB, "qual", name + ".png")
    rasterise(lines, png)
    got = lum_grid(png, name, w, h)
    ref = lum_grid(img, "ref_" + name, w, h)
    rmse, rs, rf, mg, mr = stats(got, ref)
    return dict(
        tool=name, cols=w, rows=h, rmse=round(rmse, 2), rmse_struct=round(rs, 4),
        rmse_fit=round(rf, 2), out_mean=round(mg, 1), ref_mean=round(mr, 1),
    )


TESTS = {
    "ours-ramp": [OURS, "--width", "80"],
    "ours-ramp-dither": [OURS, "--width", "80", "--dither", "diffusion"],
    "ours-braille": [OURS, "--width", "80", "--mode", "braille"],
    "ours-braille-dither": [OURS, "--width", "80", "--mode", "braille", "--dither", "diffusion"],
    "ours-block": [OURS, "--width", "80", "--mode", "block"],
    "ours-edges": [OURS, "--width", "80", "--mode", "edges"],
    "jp2a": ["jp2a", "--width=80"],
    "jp2a-invert": ["jp2a", "--width=80", "--invert"],
    "jp2a-edges": ["jp2a", "--width=80", "--edge-threshold=0.2"],
    "aic": ["ascii-image-converter", "-W", "80", "-g"],
    "aic-complex": ["ascii-image-converter", "-W", "80", "-g", "-c"],
    "aic-braille": ["ascii-image-converter", "-W", "80", "-b"],
    "aic-braille-dither": ["ascii-image-converter", "-W", "80", "-b", "--dither"],
    "chafa-ascii": ["chafa", "-f", "symbols", "--size", "80x200", "--scale", "max",
                    "--symbols", "ascii", "-c", "none"],
    "chafa-def": ["chafa", "-f", "symbols", "--size", "80x200", "--scale", "max", "-c", "none"],
    "chafa-braille": ["chafa", "-f", "symbols", "--size", "80x200", "--scale", "max",
                      "--symbols", "braille", "-c", "none"],
    "chafa-block": ["chafa", "-f", "symbols", "--size", "80x200", "--scale", "max",
                    "--symbols", "block", "-c", "none"],
    "chafa-vhalf": ["chafa", "-f", "symbols", "--size", "80x200", "--scale", "max",
                    "--symbols", "vhalf", "-c", "none"],
    "img2txt": ["img2txt", "-W", "80", "-f", "utf8", "-d", "none"],
    "catimg": ["catimg", "-w", "80"],
}

if __name__ == "__main__":
    imgs = sys.argv[1:] or ["qual/photo.png"]
    for IMG in imgs:
        print(f"=== {IMG} ===")
        res = [compare(k, v, IMG) for k, v in TESTS.items()]
        print(
            f"{'tool':22s} {'grid':9s} {'RMSE':>7s} {'RMSE_struct':>12s} "
            f"{'RMSE_fit':>9s} {'out_mean':>9s} {'ref_mean':>9s}"
        )
        for r in sorted(res, key=lambda r: r.get("rmse_struct", 1e9)):
            if "error" in r:
                print(f"{r['tool']:22s} {r['error']}")
                continue
            print(
                f"{r['tool']:22s} {str(r['cols'])+'x'+str(r['rows']):9s} {r['rmse']:7.2f} "
                f"{r['rmse_struct']:12.4f} {r['rmse_fit']:9.2f} {r['out_mean']:9.1f} "
                f"{r['ref_mean']:9.1f}"
            )
        print()
