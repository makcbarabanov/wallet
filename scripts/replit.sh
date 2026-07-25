#!/usr/bin/env bash
# Replit / cloud entrypoint: API + static frontend build served by uvicorn...
# For simplicity we run API and Vite preview side by side.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

python3 -m venv backend/.venv 2>/dev/null || true
backend/.venv/bin/pip install -q -r backend/requirements.txt

if [ ! -d frontend/node_modules ]; then
  (cd frontend && npm install)
fi

(cd frontend && npm run build)

# Serve API; frontend build can be opened via `npm run preview` or copied.
(
  cd backend
  .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
) &
(
  cd frontend
  npx vite preview --host 0.0.0.0 --port 5173
) &
wait
