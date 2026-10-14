#!/bin/sh
# Regenerate the SVGs and render the PNGs with headless Chrome (macOS path; set CHROME elsewhere),
# then intro.mp4 and intro.gif when ffmpeg is installed.
#   ./render.sh
set -eu
cd "$(dirname "$0")"
CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
PROFILE="$(mktemp -d)"
trap 'rm -rf "$PROFILE"' EXIT

shot() { # <html-or-svg> <width> <height> <out.png>
  "$CHROME" --headless=new --disable-gpu --hide-scrollbars --user-data-dir="$PROFILE" \
    --allow-file-access-from-files --default-background-color=00000000 \
    --window-size="$2,$3" --screenshot="$PWD/$4" "file://$PWD/$1" >/dev/null 2>&1
  echo "rendered $4"
}

page() { # <svg> <size> -> a page showing the svg at exactly <size> px
  printf '<html><body style="margin:0"><img src="%s" width="%s" height="%s" style="display:block"></body></html>' "$1" "$2" "$2"
}

python3 make_logo.py
page icon.svg 1024 > .icon-1024.html && shot .icon-1024.html 1024 1024 icon-1024.png
page icon.svg 512 > .icon-512.html && shot .icon-512.html 512 512 icon-512.png
shot social-preview.html 1280 640 social-preview.png
rm -f .icon-1024.html .icon-512.html

if command -v ffmpeg >/dev/null 2>&1; then
  CHROME="$CHROME" python3 render_animation.py
else
  echo "skipped intro.mp4 and intro.gif: ffmpeg not found"
fi
