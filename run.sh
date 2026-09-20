#!/usr/bin/env bash
set -euo pipefail
ordverk_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ ! -x "$ordverk_root/.venv/bin/ordverk" ]]; then
  printf 'Kör först %s/scripts/install.sh\n' "$ordverk_root" >&2
  exit 1
fi
exec "$ordverk_root/.venv/bin/ordverk" "$@"
