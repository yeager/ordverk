#!/usr/bin/env bash
set -euo pipefail
ordverk_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ordverk_python="${ORDVERK_PYTHON:-/usr/bin/python3}"
"$ordverk_python" -c 'import gi; gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1"); from gi.repository import Gtk, Adw; assert (Gtk.get_major_version(), Gtk.get_minor_version()) >= (4, 10); assert (Adw.get_major_version(), Adw.get_minor_version()) >= (1, 4)'
"$ordverk_python" -m venv --system-site-packages "$ordverk_root/.venv"
"$ordverk_root/.venv/bin/python" -m pip install \
  'l10n-lint @ git+https://github.com/yeager/l10n-lint.git@27ee6ae7860514ddbaaaa08d3cb06c8ead28f69f' \
  'svlang @ git+https://github.com/yeager/svlang.git@5c54d9ec29a3e8dd0158ed6d33498830f823617f'
"$ordverk_root/.venv/bin/python" -m pip install -e "$ordverk_root[secrets]"
if [[ "${1:-}" == "--desktop" ]]; then
  "$ordverk_root/.venv/bin/python" "$ordverk_root/scripts/install_desktop.py"
fi
printf 'Ordverk är installerat. Starta med: %s/run.sh\n' "$ordverk_root"
