#!/usr/bin/env bash
# One-command entry; dependencies remain outside the iCloud workspace.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
GEO_ENV="${SENSE_GEO_VENV:-/tmp/gba-venv}"
if [[ ! -x "$GEO_ENV/bin/python" ]]; then
    python3 -m venv "$GEO_ENV"
fi
if ! "$GEO_ENV/bin/python" -c 'import shapely' >/dev/null 2>&1; then
    "$GEO_ENV/bin/python" -m pip install -r "$SCRIPT_DIR/../requirements-geodata.txt"
fi
exec "$GEO_ENV/bin/python" "$SCRIPT_DIR/building_pipeline.py" "$@"
