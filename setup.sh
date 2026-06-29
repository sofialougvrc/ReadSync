#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if [ ! -f ".env" ]; then
  cp .env.example .env
fi

PYTHON_BIN="${PYTHON_BIN:-python3.12}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  PYTHON_BIN="python3"
fi

echo "ReadSync full-stack setup"
echo "Using Python: $($PYTHON_BIN --version)"

if [ -x ".venv/bin/python" ]; then
  VENV_VERSION="$(".venv/bin/python" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "unknown")"
  if [ "$VENV_VERSION" != "3.12" ]; then
    echo "Existing .venv uses Python $VENV_VERSION; recreating it with Python 3.12."
    python3 - <<'PY'
from pathlib import Path
import shutil
path = Path(".venv")
if path.exists():
    shutil.rmtree(path)
PY
  fi
fi

"$PYTHON_BIN" -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r backend/requirements.txt
python scripts/check_full_stack.py

cd frontend
npm install
npm run build
cd "$ROOT_DIR"

echo "ReadSync is ready."
echo "Starting full FastAPI server at http://127.0.0.1:8000"
cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
