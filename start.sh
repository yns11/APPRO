#!/usr/bin/env bash
# Entrypoint of the Databricks App: (re)build the frontend if needed, then start uvicorn on the
# port injected by the platform. Binds 0.0.0.0 (mandatory on Databricks Apps).
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -f client/dist/index.html ]; then
  if command -v npm >/dev/null 2>&1; then
    echo "[start] client/dist missing – building the frontend"
    (cd client && npm ci --no-audit --no-fund && npm run build)
  else
    echo "[start] WARNING: client/dist missing and npm unavailable – API only"
  fi
fi
export PYTHONPATH="${PYTHONPATH:-}:$(pwd)/backend"
exec uvicorn appro.api.main:app --host 0.0.0.0 --port "${DATABRICKS_APP_PORT:-8000}" --workers 1 --timeout-graceful-shutdown 10
