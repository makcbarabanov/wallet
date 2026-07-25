#!/usr/bin/env bash
# Start backend + frontend for local development.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Node via nvm if present
export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
# shellcheck disable=SC1091
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"

if [ ! -d backend/.venv ]; then
  python3 -m venv backend/.venv
  backend/.venv/bin/pip install -r backend/requirements.txt
fi

if [ ! -d frontend/node_modules ]; then
  (cd frontend && npm install)
fi

cleanup() {
  kill 0 2>/dev/null || true
}
trap cleanup EXIT

echo "→ Backend  http://127.0.0.1:8000"
(
  cd backend
  .venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
) &

echo "→ Frontend http://127.0.0.1:5173"
(
  cd frontend
  npm run dev -- --host 127.0.0.1
) &

wait
