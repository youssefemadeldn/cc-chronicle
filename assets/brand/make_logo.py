#!/usr/bin/env python3
"""Generate the cc-chronicle logo files (stdlib only). Re-run after changing the constants.

The mark is a tree-ring cross-section: a core and three growth rings, off-centre so they
widen toward the upper right like real wood, with one honey-amber arc on the outer ring
marking a moment in time. Each ring is its own <path> so the mark can be animated ring by
ring. Small sizes (≤ 32 px) use a simplified mark: two rings, no wobble, bolder strokes.
"""
from __future__ import annotations

import math
from pathlib import Path

OUT = Path(__file__).resolve().parent

NAVY = "#14213D"
PAPER = "#F5F0E6"
AMBER = "#F2A33A"

SIZE = 512
CORNER = 112  # rounded-square radius
# Shape of the full-size mark: ring radii, how far each ring's centre moves up-right
# (eccentric growth), and the wobble amplitude (fraction of the radius).
RADII = (62, 114, 166)
SHIFT = 8
WOBBLE = 0.015

FONT = "ui-monospace, SFMono-Regular, Menlo, Consolas, 'Liberation Mono', monospace"


def ring_path(cx, cy, r, wobble, phase, start=0.0, end=360.0, points=240) -> str:
    """A circle (or arc, in degrees, 0° = east, clockwise on screen) with a gentle organic wobble."""
    closed = end - start >= 360
    steps = points if closed else max(12, int(points * (end - start) / 360))
    coords = []
    for i in range(steps + (0 if closed else 1)):
        t = math.radians(start + (end - start) * i / steps)
        rr = r * (1 + wobble * math.sin(3 * t + phase) + wobble * 0.3 * math.sin(7 * t + 2.1 * phase))
        coords.append(f"{cx + rr * math.cos(t):.2f},{cy + rr * math.sin(t):.2f}")
    return "M" + " L".join(coords) + (" Z" if closed else "")


def ring_band(cx, cy, r, half, wobble, phase, points=240) -> str:
    """A ring drawn as a filled band (outer edge + inner edge, even-odd fill).

    Filled rather than stroked on purpose: a stroked polyline of many tiny segments leaves
    hairline cracks at its joins in some renderers (seen in Chrome/Skia exports).
    """
    def edge(offset, reverse):
        pts = []
        for i in range(points):
            t = 2 * math.pi * i / points
            rr = r * (1 + wobble * math.sin(3 * t + phase) + wobble * 0.3 * math.sin(7 * t + 2.1 * phase)) + offset
            pts.append(f"{cx + rr * math.cos(t):.2f},{cy + rr * math.sin(t):.2f}")
        if reverse:
            pts.reverse()
        return "M" + " L".join(pts) + " Z"
    return edge(half, False) + " " + edge(-half, True)


def mark_shapes(small: bool, ring_color: str) -> str:
    """Rings, core and amber arc, drawn in a 512×512 box."""
    core_x, core_y = 236, 278          # core sits toward the lower left
    shift = (SHIFT, -SHIFT)            # each ring's centre moves up-right: eccentric growth
    if small:
        radii, stroke, core_r, wobble = (92, 162), 44, 40, 0.0
    else:
        radii, stroke, core_r, wobble = RADII, 26, 30, WOBBLE
    parts = [f'<circle id="core" cx="{core_x}" cy="{core_y}" r="{core_r}" fill="{ring_color}"/>']
    outer = None
    for i, r in enumerate(radii, 1):
        cx, cy = core_x + shift[0] * i, core_y + shift[1] * i
        d = ring_band(cx, cy, r, stroke / 2, wobble, phase=1.3 * i)
        parts.append(f'<path id="ring-{i}" d="{d}" fill="{ring_color}" fill-rule="evenodd"/>')
        outer = (cx, cy, r, 1.3 * i)
    cx, cy, r, phase = outer
    arc = ring_path(cx, cy, r, wobble, phase, start=-78, end=-22)
    parts.append(f'<path id="marker" d="{arc}" fill="none" stroke="{AMBER}" stroke-width="{stroke}" '
                 f'stroke-linecap="round"/>')
    return "\n  ".join(parts)


def svg(body: str, width: int, height: int, title: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img" aria-label="{title}">\n'
            f'  <title>{title}</title>\n  {body}\n</svg>\n')


def icon(small: bool = False) -> str:
    """Navy rounded square with the paper-coloured mark: avatar, favicon, plugin listing."""
    body = (f'<rect width="{SIZE}" height="{SIZE}" rx="{CORNER}" fill="{NAVY}"/>\n  '
            + mark_shapes(small, PAPER))
    return svg(body, SIZE, SIZE, "cc-chronicle")


def bare_mark(ring_color: str) -> str:
    """The mark without its square, for placing on a coloured background."""
    return svg(mark_shapes(False, ring_color), SIZE, SIZE, "cc-chronicle")


def lockup(text_color: str) -> str:
    """Icon + wordmark, for the README header. Light: navy text. Dark: paper text."""
    height, icon_size, gap = 160, 160, 36
    scale = icon_size / SIZE
    body = (f'<g transform="scale({scale:.4f})">'
            f'<rect width="{SIZE}" height="{SIZE}" rx="{CORNER}" fill="{NAVY}"/>{mark_shapes(False, PAPER)}</g>\n  '
            f'<text x="{icon_size + gap}" y="{height / 2 + 26}" font-family="{FONT}" font-size="76" '
            f'font-weight="700" letter-spacing="-1" fill="{text_color}">cc<tspan fill="{AMBER}">-</tspan>chronicle</text>')
    return svg(body, 840, height, "cc-chronicle")


def main() -> None:
    files = {
        "icon.svg": icon(),
        "icon-small.svg": icon(small=True),
        "mark-on-light.svg": bare_mark(NAVY),
        "mark-on-dark.svg": bare_mark(PAPER),
        "lockup-light.svg": lockup(NAVY),
        "lockup-dark.svg": lockup(PAPER),
    }
    for name, content in files.items():
        (OUT / name).write_text(content)
        print(f"wrote {name}")


if __name__ == "__main__":
    main()
