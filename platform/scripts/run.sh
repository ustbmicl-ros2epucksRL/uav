#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
if [ -n "${SENSE_PYTHON:-}" ]; then
  interpreter=$SENSE_PYTHON
elif [ -x ../.venv/bin/python ]; then
  interpreter=../.venv/bin/python
elif [ -x "$HOME/Library/Caches/sensing-venv/bin/python" ]; then
  interpreter="$HOME/Library/Caches/sensing-venv/bin/python"
else
  interpreter=.venv/bin/python
fi
if [ ! -x "$interpreter" ]; then
  echo '未找到 Python 环境，请设置 SENSE_PYTHON 指向已安装 requirements.txt 的本机环境。' >&2
  exit 1
fi
case "$interpreter" in /*) ;; *) interpreter="$(pwd)/$interpreter" ;; esac
export PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
export PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-${TMPDIR:-/tmp}/sensing-pycache}"
case "${SENSE_SERVICE:-planning}" in
  planning) port=${SENSE_PORT:-8767} ;;
  flight) cd ubuntu/flight; export FLIGHT_RECORDS_DIR="${FLIGHT_RECORDS_DIR:-$PWD/data/flight-archives}"; port=${SENSE_PORT:-8772} ;;
  lowair) cd ubuntu/lowair; port=${SENSE_PORT:-8771} ;;
  *) echo 'SENSE_SERVICE 只支持 planning、flight 或 lowair' >&2; exit 1 ;;
esac
# Bound dependency probing so cloud-only modules cannot silently hang startup.
"$interpreter" - <<'PY'
import subprocess, sys
try:
    subprocess.run([sys.executable, '-c', 'import fastapi, uvicorn, numpy, scipy, httpx, pypdf, multipart; import app.main'], check=True, timeout=20)
except subprocess.TimeoutExpired:
    sys.exit('依赖读取超过 20 秒。请将虚拟环境移出 iCloud，并设置 SENSE_PYTHON。')
except subprocess.CalledProcessError:
    sys.exit('运行依赖不完整，请使用所选 Python 安装 requirements.txt。')
PY
exec "$interpreter" -m uvicorn app.main:app --host "${SENSE_HOST:-127.0.0.1}" --port "$port"
