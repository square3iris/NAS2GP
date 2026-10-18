#!/bin/sh
# ==================================================
#  NAS2GP テスト 実行 (macOS / Linux)
#  NAS2GP test runner for macOS / Linux
# ==================================================
set -eu

APP_HOME=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$APP_HOME"

# .venv が あれ ば そちら 、 無けれ ば PATH 上 の python3 を 使用
# Use .venv if present, otherwise python3 on PATH
PY=python3
if [ -x "$APP_HOME/.venv/bin/python" ]; then
    PY="$APP_HOME/.venv/bin/python"
fi

export PYTHONPATH="$APP_HOME${PYTHONPATH:+:$PYTHONPATH}"

exec "$PY" -m pytest -v "$@"
