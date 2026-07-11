#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")"

PYTHON_BIN="${PYTHON_BIN:-python3}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python 3 was not found. Install Python 3.11 or newer and try again."
  read -k 1 "?Press any key to close…"
  exit 1
fi

if ! "$PYTHON_BIN" - <<'PY'
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
then
  echo "Northstar requires Python 3.11 or newer."
  "$PYTHON_BIN" --version || true
  read -k 1 "?Press any key to close…"
  exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
  echo "Creating Northstar virtual environment…"
  "$PYTHON_BIN" -m venv .venv
fi

REQ_HASH=$(shasum -a 256 requirements.txt | awk '{print $1}')
STAMP_FILE=".venv/.northstar-requirements"
INSTALLED_HASH=""
if [ -f "$STAMP_FILE" ]; then
  INSTALLED_HASH=$(cat "$STAMP_FILE")
fi

if [ "$REQ_HASH" != "$INSTALLED_HASH" ]; then
  echo "Installing Northstar dependencies…"
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt
  echo "$REQ_HASH" > "$STAMP_FILE"
fi

if ! .venv/bin/python - <<'PY'
import flask, sqlalchemy, dotenv
PY
then
  echo "The virtual environment is incomplete. Rebuilding it…"
  rm -rf .venv
  "$PYTHON_BIN" -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt
  REQ_HASH=$(shasum -a 256 requirements.txt | awk '{print $1}')
  echo "$REQ_HASH" > .venv/.northstar-requirements
fi

exec .venv/bin/python scripts/dev.py
