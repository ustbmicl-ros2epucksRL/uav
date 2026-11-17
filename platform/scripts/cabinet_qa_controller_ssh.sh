#!/usr/bin/env bash
# Remote SSH key must have a forced command for the dedicated QA controller.
set -euo pipefail
[[ ( $# == 3 || $# == 4 ) && "$1" =~ ^(approved|mismatch|offline|restore|observe-approved|observe-denied)$ &&
   "$2" =~ ^(snp|tdx)$ && "$3" =~ ^[0-9a-f]{32}$ ]] || exit 2
if [[ "$1" == observe-* ]]; then [[ ${4:-} =~ ^[0-9a-f]{32}$ ]]; else [[ $# == 3 ]]; fi
exec ssh -T -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=5 \
    cabinet-qa-control "$@"
