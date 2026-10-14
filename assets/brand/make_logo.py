#!/usr/bin/env python3
"""Generate the cc-chronicle logo files (stdlib only). Re-run after changing the constants.

The mark is a tree-ring cross-section: a core and three growth rings, off-centre so they
widen toward the upper right like real wood, with one honey-amber arc on the outer ring
marking a moment in time. Each ring is its own <path> so the mark can be animated ring by
ring. Small sizes (≤ 32 px) use a simplified mark: two rings, no wobble, bolder strokes.

The *-animated.svg files build in about 2.8 s: the core pops in, rings 1→3 grow out of the
one before, the amber marker draws clockwise, then the wordmark types in. They hold, fade out
and replay every 10 s. Viewers who ask for reduced motion see the static mark.
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
# previous ring's radius (the core's, for ring 1) to its own. The build then holds, fades out
# over FADE and replays every CYCLE: GitHub shows the file list above the README, and an SVG
# inside <img> can't tell when it scrolls into view, so a single play is usually missed.
CYCLE, FADE = 10.0, 0.4
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
    if animated:  # the mark fades out before each replay; the square behind it stays
        return '<g class="fade">' + "\n  ".join(parts) + "</g>"
    return "\n  ".join(parts)


def pct(t: float) -> str:
    """A time in seconds as a keyframe position within one CYCLE."""
    return f"{100 * t / CYCLE:.2f}".rstrip("0").rstrip(".") + "%"


def keyframes(name: str, stops) -> str:
    """@keyframes over one CYCLE, from (times in seconds, declarations) pairs."""
    body = " ".join(f"{', '.join(dict.fromkeys(pct(t) for t in times))} {{ {css} }}" for times, css in stops)
    return f"@keyframes {name} {{ {body} }}"


def animation_css(small: bool = False, typed: bool = False) -> str:
    """Keyframes for the *-animated.svg files.

    Every animation lasts one CYCLE with no delay and repeats, so the parts stay in step: each
    one's keyframes hold its starting state until its moment in the build. Rings are filled
    bands, so a dash offset can't draw them: each one scales up from its own centre and fades
    in. Only the marker, a stroke, is drawn. Everything rests in its final state, so
    `animation: none` (reduced motion) leaves the static mark.
    """
    core_r, _, _, rings = geometry(small)
    loop = f"{CYCLE:g}s"
    rules = [
        keyframes("pop", [((0,), "transform: scale(0);"),
                          ((CORE_POP * 0.6,), "transform: scale(1.12);"),
                          ((CORE_POP, CYCLE), "transform: scale(1);")]),
        f"#core {{ transform-box: view-box; transform-origin: {CORE_X}px {CORE_Y}px; "
        f"animation: pop {loop} ease-out infinite; }}",
    ]
    previous = core_r
    for i, (cx, cy, r) in enumerate(rings, 1):
        start = RING_START + RING_STAGGER * (i - 1)
        rules += [
            keyframes(f"grow-{i}", [((0, start), f"transform: scale({previous / r:.3f}); opacity: 0;"),
                                    ((start + RING_GROW * 0.6,), "opacity: 1;"),
                                    ((start + RING_GROW, CYCLE), "transform: scale(1); opacity: 1;")]),
            f"#ring-{i} {{ transform-box: view-box; transform-origin: {cx}px {cy}px; "
            f"animation: grow-{i} {loop} {EASE_OUT} infinite; }}",
        ]
        previous = r
    rules += [
        keyframes("draw", [((0, MARKER_START), "stroke-dashoffset: 1; opacity: 0;"),
                           ((MARKER_START + 0.005,), "opacity: 1;"),
                           ((MARKER_START + MARKER_DRAW, CYCLE), "stroke-dashoffset: 0; opacity: 1;")]),
        f"#marker {{ stroke-dasharray: 1; animation: draw {loop} ease-in-out infinite; }}",
    ]
    if typed:
        for k in range(1, len(WORDMARK) + 1):
            start = TYPE_START + TYPE_STEP * (k - 1)
            rules += [
                keyframes(f"type-{k}", [((0, start), "fill-opacity: 0;"), ((start + 0.05, CYCLE), "fill-opacity: 1;")]),
                f"#char-{k} {{ animation: type-{k} {loop} linear infinite; }}",
            ]
    rules += [
        keyframes("fade", [((0, CYCLE - FADE), "opacity: 1;"), ((CYCLE,), "opacity: 0;")]),
        f".fade {{ animation: fade {loop} ease-in infinite; }}",
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
    for k, ch in enumerate(WORDMARK, 1):
        fill = f' fill="{AMBER}"' if ch == "-" else ""
        spans.append(f'<tspan id="char-{k}"{fill}>{ch}</tspan>')
    return "".join(spans)  # no whitespace between the tspans: it would render as spaces


def lockup(text_color: str, animated: bool = False) -> str:
    """Icon + wordmark, for the README header. Light: navy text. Dark: paper text."""
    height, icon_size, gap = 160, 160, 36
    scale = icon_size / SIZE
    fade = ' class="fade"' if animated else ""  # the wordmark fades out with the mark before each replay
    body = (f'<g transform="scale({scale:.4f})">'
            f'<rect width="{SIZE}" height="{SIZE}" rx="{CORNER}" fill="{NAVY}"/>{mark_shapes(False, PAPER, animated)}</g>\n  '
            f'<text{fade} x="{icon_size + gap}" y="{height / 2 + 26}" font-family="{FONT}" font-size="76" '
            f'font-weight="700" letter-spacing="-1" fill="{text_color}">{wordmark(animated)}</text>')
    return svg(body, 840, height, "cc-chronicle", animation_css(typed=True) if animated else "")


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
