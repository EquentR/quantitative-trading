#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${QT_VENV_DIR:-.venv}"

if [ ! -x "$VENV_DIR/bin/python" ]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/python" -m pip install -e ".[dev]"

export QT_DATABASE_PATH="${QT_DATABASE_PATH:-$ROOT_DIR/data/quant_trading.db}"
mkdir -p "$(dirname "$QT_DATABASE_PATH")"

exec "$VENV_DIR/bin/qt" service check
