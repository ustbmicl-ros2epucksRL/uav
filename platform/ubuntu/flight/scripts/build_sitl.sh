#!/usr/bin/env bash
set -euo pipefail
# Keep the native build out of iCloud. Requires clang/g++ and network access.
SITL_SOURCE_DIR=${SITL_SOURCE_DIR:-/tmp/lowair-sitl-source}
SITL_PYTHON=${SITL_PYTHON:-/tmp/lowair-workflow-venv/bin/python}
if [[ ! -d "$SITL_SOURCE_DIR/.git" ]]; then
    git clone --filter=blob:none --depth 1 --branch Copter-4.6.3 --no-checkout https://github.com/ArduPilot/ardupilot.git "$SITL_SOURCE_DIR"
    git -C "$SITL_SOURCE_DIR" sparse-checkout init --no-cone
    printf '/*\n!/libraries/AP_HAL_ChibiOS/\n!/libraries/AP_HAL_Linux/\n!/Tools/bootloaders/\n!/docs/\n' > "$SITL_SOURCE_DIR/.git/info/sparse-checkout"
    git -C "$SITL_SOURCE_DIR" checkout Copter-4.6.3
fi
cd "$SITL_SOURCE_DIR"
git sparse-checkout add '/libraries/AP_HAL_ChibiOS/hwdef/scripts/' '/libraries/AP_HAL_ChibiOS/*.h' '/libraries/AP_HAL_ChibiOS/*.cpp' '/libraries/AP_HAL_Linux/*.h'
git submodule update --init --depth 1 modules/waf modules/mavlink modules/DroneCAN/DSDL modules/DroneCAN/dronecan_dsdlc modules/DroneCAN/libcanard modules/DroneCAN/pydronecan
git -C modules/mavlink submodule update --init --depth 1 pymavlink
"$SITL_PYTHON" -m pip install 'empy==3.3.4' 'setuptools<81' pexpect future dronecan pymavlink
"$SITL_PYTHON" waf configure --board sitl --disable-tests
"$SITL_PYTHON" waf copter -j4
