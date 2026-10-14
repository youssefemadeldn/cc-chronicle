#!/usr/bin/env python3
"""Generate the cc-chronicle logo files (stdlib only). Re-run after changing the constants.

The mark is a tree-ring cross-section: a core and three growth rings, off-centre so they
widen toward the upper right like real wood, with one honey-amber arc on the outer ring
marking a moment in time. Each ring is its own <path> so the mark can be animated ring by
ring. Small sizes (≤ 32 px) use a simplified mark: two rings, no wobble, bolder strokes.

The *-animated.svg files play once (about 2.8 s) and hold: the core pops in, rings 1→3 grow
out of the one before, the amber marker draws clockwise, then the wordmark types in. Viewers
who ask for reduced motion see the static mark.
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
CORE_X, CORE_Y = 236, 278  # the core sits toward the lower left

FONT = "ui-monospace, SFMono-Regular, Menlo, Consolas, 'Liberation Mono', monospace"
WORDMARK = "cc-chronicle"

# Animation timeline, in seconds. Ring i starts RING_STAGGER after ring i-1 and grows from the
# previous ring's radius (the core's, for ring 1) to its own.
CORE_POP = 0.3
RING_START, RING_STAGGER, RING_GROW = 0.3, 0.3, 0.5
MARKER_START, MARKER_DRAW = 1.4, 0.5
TYPE_START, TYPE_STEP = 1.9, 0.075  # one character every 75 ms: the wordmark ends at 2.8 s
EASE_OUT = "cubic-bezier(0.22, 1, 0.36, 1)"


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


def geometry(small: bool):
    """Core radius, ring stroke, wobble, and each ring's (centre x, centre y, radius)."""
    if small:
        radii, stroke, core_r, wobble = (92, 162), 44, 40, 0.0
    else:
        radii, stroke, core_r, wobble = RADII, 26, 30, WOBBLE
    # each ring's centre moves up-right: eccentric growth
    rings = [(CORE_X + SHIFT * i, CORE_Y - SHIFT * i, r) for i, r in enumerate(radii, 1)]
    return core_r, stroke, wobble, rings


def mark_shapes(small: bool, ring_color: str, animated: bool = False) -> str:
    """Rings, core and amber arc, drawn in a 512×512 box."""
    core_r, stroke, wobble, rings = geometry(small)
    parts = [f'<circle id="core" cx="{CORE_X}" cy="{CORE_Y}" r="{core_r}" fill="{ring_color}"/>']
    for i, (cx, cy, r) in enumerate(rings, 1):
        d = ring_band(cx, cy, r, stroke / 2, wobble, phase=1.3 * i)
        parts.append(f'<path id="ring-{i}" d="{d}" fill="{ring_color}" fill-rule="evenodd"/>')
    cx, cy, r = rings[-1]
    arc = ring_path(cx, cy, r, wobble, 1.3 * len(rings), start=-78, end=-22)
    # pathLength="1" lets the animation draw the arc with a dash offset going from 1 to 0
    length = ' pathLength="1"' if animated else ""
    parts.append(f'<path id="marker" d="{arc}"{length} fill="none" stroke="{AMBER}" stroke-width="{stroke}" '
                 f'stroke-linecap="round"/>')
    return "\n  ".join(parts)


def animation_css(small: bool = False) -> str:
    """Keyframes for the *-animated.svg files.

    Rings are filled bands, so a dash offset can't draw them: each one scales up from its own
    centre and fades in. Only the marker, a stroke, is drawn. Everything rests in its final
    state, so `animation: none` (reduced motion) leaves the static mark.
    """
    core_r, _, _, rings = geometry(small)
    rules = [
        "@keyframes pop { 0% { transform: scale(0); } 60% { transform: scale(1.12); } 100% { transform: scale(1); } }",
        f"#core {{ transform-box: view-box; transform-origin: {CORE_X}px {CORE_Y}px; "
        f"animation: pop {CORE_POP}s ease-out both; }}",
    ]
    previous = core_r
    for i, (cx, cy, r) in enumerate(rings, 1):
        start = RING_START + RING_STAGGER * (i - 1)
        rules += [
            f"@keyframes grow-{i} {{ 0% {{ transform: scale({previous / r:.3f}); opacity: 0; }} "
            f"60% {{ opacity: 1; }} 100% {{ transform: scale(1); opacity: 1; }} }}",
            f"#ring-{i} {{ transform-box: view-box; transform-origin: {cx}px {cy}px; "
            f"animation: grow-{i} {RING_GROW}s {EASE_OUT} {start:.2f}s both; }}",
        ]
        previous = r
    rules += [
        "@keyframes draw { 0% { stroke-dashoffset: 1; opacity: 0; } 1% { opacity: 1; } "
        "100% { stroke-dashoffset: 0; opacity: 1; } }",
        f"#marker {{ stroke-dasharray: 1; animation: draw {MARKER_DRAW}s ease-in-out {MARKER_START}s both; }}",
        "@keyframes type { from { fill-opacity: 0; } to { fill-opacity: 1; } }",
        ".type { animation: type 0.05s linear both; }",
        "@media (prefers-reduced-motion: reduce) { * { animation: none !important; } }",
    ]
    return "\n    ".join(rules)


def svg(body: str, width: int, height: int, title: str, css: str = "") -> str:
    style = f"  <style>\n    {css}\n  </style>\n" if css else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img" aria-label="{title}">\n'
            f'  <title>{title}</title>\n{style}  {body}\n</svg>\n')


def icon(small: bool = False, animated: bool = False) -> str:
    """Navy rounded square with the paper-coloured mark: avatar, favicon, plugin listing."""
    body = (f'<rect width="{SIZE}" height="{SIZE}" rx="{CORNER}" fill="{NAVY}"/>\n  '
            + mark_shapes(small, PAPER, animated))
    return svg(body, SIZE, SIZE, "cc-chronicle", animation_css(small) if animated else "")


def bare_mark(ring_color: str) -> str:
    """The mark without its square, for placing on a coloured background."""
    return svg(mark_shapes(False, ring_color), SIZE, SIZE, "cc-chronicle")


def wordmark(animated: bool) -> str:
    """The wordmark's text, hyphen in amber. Animated: one <tspan> per character, so it types in."""
    if not animated:
        return f'cc<tspan fill="{AMBER}">-</tspan>chronicle'
    spans = []
    for k, ch in enumerate(WORDMARK):
        fill = f' fill="{AMBER}"' if ch == "-" else ""
        spans.append(f'<tspan class="type"{fill} style="animation-delay: {TYPE_START + TYPE_STEP * k:.3f}s">{ch}</tspan>')
    return "".join(spans)  # no whitespace between the tspans: it would render as spaces


def lockup(text_color: str, animated: bool = False) -> str:
    """Icon + wordmark, for the README header. Light: navy text. Dark: paper text."""
    height, icon_size, gap = 160, 160, 36
    scale = icon_size / SIZE
    body = (f'<g transform="scale({scale:.4f})">'
            f'<rect width="{SIZE}" height="{SIZE}" rx="{CORNER}" fill="{NAVY}"/>{mark_shapes(False, PAPER, animated)}</g>\n  '
            f'<text x="{icon_size + gap}" y="{height / 2 + 26}" font-family="{FONT}" font-size="76" '
            f'font-weight="700" letter-spacing="-1" fill="{text_color}">{wordmark(animated)}</text>')
    return svg(body, 840, height, "cc-chronicle", animation_css() if animated else "")


def main() -> None:
    files = {
        "icon.svg": icon(),
        "icon-small.svg": icon(small=True),
        "mark-on-light.svg": bare_mark(NAVY),
        "mark-on-dark.svg": bare_mark(PAPER),
        "lockup-light.svg": lockup(NAVY),
        "lockup-dark.svg": lockup(PAPER),
        "icon-animated.svg": icon(animated=True),
        "lockup-animated-light.svg": lockup(NAVY, animated=True),
        "lockup-animated-dark.svg": lockup(PAPER, animated=True),
    }
    for name, content in files.items():
        (OUT / name).write_text(content)
        print(f"wrote {name}")


if __name__ == "__main__":
    main()
