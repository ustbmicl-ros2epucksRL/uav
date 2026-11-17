#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
exec "${SENSE_PYTHON:-python3}" scripts/cabinet_guest_acceptance.py "$@"
