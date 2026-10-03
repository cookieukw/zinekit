#!/usr/bin/env bash
# zinekit launcher.  The first run creates .venv/ here and installs PySide6-Essentials + Pillow.
#
#   ./run.sh [FILES...]          open the editor (an image, or files for the Batch tab)
#   ./run.sh apply|text|...      command line, see ./run.sh --help
#   ./run.sh --selftest          run the tests, the editor included (Qt offscreen)
#   ./run.sh --update            reinstall / upgrade the Python packages
#   ./run.sh --install-desktop   add zinekit to the application menu
#
# PYTHON=python3.12 ./run.sh    use another Python for the virtual environment
set -euo pipefail

HERE="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
VENV="$HERE/.venv"
PY="${PYTHON:-python3}"
STAMP="$VENV/.zinekit-requirements"

say() { printf 'zinekit: %s\n' "$*" >&2; }

needs_setup() {
  [ ! -x "$VENV/bin/python" ] || ! cmp -s "$HERE/requirements.txt" "$STAMP"
}

setup() {
  if [ ! -x "$VENV/bin/python" ]; then
    command -v "$PY" >/dev/null 2>&1 || { say "$PY not found (set PYTHON=/path/to/python3)"; exit 1; }
    say "creating $VENV"
    if ! "$PY" -m venv "$VENV"; then
      say "could not create a virtual environment."
      say "On Debian/Ubuntu: sudo apt install python3-venv"
      rm -rf "$VENV"
      exit 1
    fi
  fi
  say "installing the Python packages (first run only, ~100 MB)…"
  "$VENV/bin/python" -m pip install --upgrade --quiet pip
  if ! "$VENV/bin/python" -m pip install --quiet -r "$HERE/requirements.txt"; then
    say "pip failed. If PySide6 has no wheel for this Python, try: PYTHON=python3.12 $0 --update"
    exit 1
  fi
  cp "$HERE/requirements.txt" "$STAMP"
}

install_desktop() {
  local apps="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
  mkdir -p "$apps"
  cat > "$apps/zinekit.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=zinekit
GenericName=Punk Zine editor
Comment=Punk zine print for images, videos and titles
Exec="$HERE/run.sh" %F
Icon=$HERE/zinekit/gui/icon.svg
Terminal=false
Categories=Graphics;AudioVideo;
MimeType=image/png;image/jpeg;image/webp;video/mp4;video/quicktime;video/webm;video/x-matroska;
StartupWMClass=zinekit
DESKTOP
  command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$apps" >/dev/null 2>&1 || true
  say "added $apps/zinekit.desktop"
}

case "${1:-}" in
  --update)
    rm -f "$STAMP"
    setup
    exit 0
    ;;
  --selftest)
    needs_setup && setup
    cd "$HERE"
    QT_QPA_PLATFORM=offscreen exec "$VENV/bin/python" -m unittest discover -s tests -t . -v
    ;;
  --install-desktop)
    install_desktop
    exit 0
    ;;
esac

if needs_setup; then
  setup
fi
export PYTHONPATH="$HERE${PYTHONPATH:+:$PYTHONPATH}"
exec "$VENV/bin/python" -m zinekit "$@"
