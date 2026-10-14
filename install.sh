#!/bin/sh
# Install cc-chronicle from this folder: link the launcher into ~/.local/bin (or $BIN_DIR).
#   ./install.sh               install, or re-link after moving the folder
#   ./install.sh --uninstall   remove the link (your config and archive are untouched)
set -eu

REPO="$(cd "$(dirname "$0")" && pwd)"
BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"
LINK="$BIN_DIR/cc-chronicle"

if [ "${1:-}" = "--uninstall" ]; then
  if [ -L "$LINK" ]; then rm "$LINK" && echo "Removed $LINK"; else echo "Nothing to remove at $LINK"; fi
  echo "If you scheduled backups, also run: cc-chronicle schedule uninstall (before removing the link)"
  exit 0
fi

command -v python3 >/dev/null 2>&1 || { echo "error: python3 is required (3.9 or newer)" >&2; exit 1; }
python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' \
  || { echo "error: Python 3.9 or newer is required (found $(python3 -V 2>&1))" >&2; exit 1; }
command -v rclone >/dev/null 2>&1 \
  || echo "warning: rclone not found. Install it before your first backup: https://rclone.org/install/"

mkdir -p "$BIN_DIR"
chmod +x "$REPO/bin/cc-chronicle"
ln -sf "$REPO/bin/cc-chronicle" "$LINK"
echo "Installed $LINK -> $REPO/bin/cc-chronicle"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "note: $BIN_DIR is not on your PATH. Add it to your shell profile:  export PATH=\"$BIN_DIR:\$PATH\"" ;;
esac
echo "Next: cc-chronicle init"
