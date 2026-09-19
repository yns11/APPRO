#!/usr/bin/env bash
# Build the frontend (client/dist) – run before `databricks apps deploy` so that the uploaded
# source already contains the static bundle.
set -euo pipefail
cd "$(dirname "$0")/.."
(cd client && npm ci --no-audit --no-fund && npm run build)
echo "frontend built in client/dist"
