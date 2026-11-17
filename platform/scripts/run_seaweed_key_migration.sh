#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
py="${SENSE_PYTHON:-python3}"
weed="${CABINET_WEED_BINARY:-$(command -v weed || true)}"
if [[ ! -x "$weed" ]]; then
    echo "Set CABINET_WEED_BINARY to an executable SeaweedFS 4.52 weed binary." >&2
    exit 2
fi
export CABINET_WEED_BINARY="$weed"
for tool in ffmpeg ffprobe; do command -v "$tool" >/dev/null || { echo "Missing $tool" >&2; exit 2; }; done
"$py" -c 'import pytest, boto3, fastapi, cryptography, PIL'
unset SENSE_CABINET_ATTESTATION_CONFIG SENSE_CABINET_CHECKPOINT_CONFIG SENSE_CABINET_OBJECT_CONFIG
export SENSE_CABINET_KEY_MODE=demo
if [[ "${CABINET_QA_KBS:?use acceptance entry point}" == trustee ]]; then
commit=22ca43188fb2db632d43d3d5a164e0edbd5ab021
export CABINET_TRUSTEE_SOURCE="${CABINET_TRUSTEE_SOURCE:-${TMPDIR:-/tmp}/cabinet-trustee-$commit}"
if [[ ! -d "$CABINET_TRUSTEE_SOURCE/.git" ]]; then
    git init -q "$CABINET_TRUSTEE_SOURCE"
    git -C "$CABINET_TRUSTEE_SOURCE" fetch --depth 1 https://github.com/confidential-containers/trustee.git "$commit"
    git -C "$CABINET_TRUSTEE_SOURCE" checkout --detach -q FETCH_HEAD
fi
docker info >/dev/null
for name in kbs kbs-client-image; do
    image="ghcr.io/confidential-containers/staged-images/$name:$commit"
    docker image inspect "$image" >/dev/null 2>&1 || docker pull "$image"
done
fi
if [[ "$(uname -s)" == Darwin ]]; then
    scratch=$(mktemp -d "${TMPDIR:-/tmp}/cabinet-cutover.XXXXXXXX")
    mount="$scratch/volume"
    mkdir "$mount"
    hdiutil create -quiet -size 2g -type SPARSE -fs HFS+ -volname CabinetCutover "$scratch/store.sparseimage"
    hdiutil attach -quiet -nobrowse -mountpoint "$mount" "$scratch/store.sparseimage"
    cleanup() {
        if hdiutil detach -quiet "$mount"; then
            rm -f "$scratch/store.sparseimage"
            rmdir "$mount" 2>/dev/null || true
            rmdir "$scratch"
        else
            echo "Unmount the test volume before cleaning $scratch" >&2
        fi
    }
    trap cleanup EXIT
    "$py" -m pytest --basetemp "$mount/acceptance" --junitxml="$CABINET_QA_JUNIT" -q -s --tb=short tests/test_cabinet_seaweed_key_cutover.py
else
    "$py" -m pytest --junitxml="$CABINET_QA_JUNIT" -q -s --tb=short tests/test_cabinet_seaweed_key_cutover.py
fi
