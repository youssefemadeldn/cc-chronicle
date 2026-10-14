# Brand assets

The cc-chronicle mark is a cross-section of a tree trunk: a core and three growth rings, off-centre so they widen toward the upper right like real wood, with a honey-amber arc on the outer ring marking a moment in time. A chronicle grows ring by ring, month by month.

| File | Use |
|---|---|
| `icon.svg`, `icon-512.png`, `icon-1024.png` | App-style icon: avatars, listings, large displays |
| `icon-small.svg` | Simplified icon for 32 px and below (two rings, bolder strokes, no wobble) |
| `mark-on-light.svg`, `mark-on-dark.svg` | The mark without its square, for placing on a background |
| `lockup-light.svg`, `lockup-dark.svg` | Icon + wordmark for light and dark backgrounds (the README header) |
| `social-preview.png` | 1280×640 image shown when the repository link is shared |

## Colours

| Name | Hex | Role |
|---|---|---|
| Ink navy | `#14213D` | Icon background, text on light |
| Paper | `#F5F0E6` | Rings, text on dark |
| Honey amber | `#F2A33A` | The marker arc and the wordmark's hyphen. Accent only |

## Editing

The SVGs are generated: change the constants in `make_logo.py` (ring radii, off-centre shift, wobble), then run `./render.sh` to rebuild the SVGs and the PNGs (needs Python 3 and Google Chrome). Each ring is a separate `<path>` (`ring-1` to `ring-3`, then `marker`), so the mark can be animated ring by ring.

Rings are filled bands rather than stroked lines: stroked polylines left hairline cracks in Chrome's PNG exports.
