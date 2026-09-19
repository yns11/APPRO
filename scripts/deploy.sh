#!/usr/bin/env bash
# Build + deploy the Databricks App with the CLI (no bundle).
#   scripts/deploy.sh <app-name> <workspace-folder> [profile]
# Example: scripts/deploy.sh appro /Workspace/Users/me@corp.com/apps/appro DEFAULT
set -euo pipefail
APP_NAME="${1:?app name}"
WS_PATH="${2:?workspace path}"
PROFILE="${3:-DEFAULT}"
cd "$(dirname "$0")/.."
scripts/build.sh
databricks apps create "$APP_NAME" --profile "$PROFILE" 2>/dev/null || true
databricks workspace mkdirs "$WS_PATH" --profile "$PROFILE"
# exclude dev-only folders (node_modules, caches, local db)
rsync -a --delete --exclude node_modules --exclude .git --exclude '__pycache__' --exclude .pytest_cache \
  --exclude data/local --exclude '.venv' ./ /tmp/appro-upload/
databricks workspace import-dir /tmp/appro-upload "$WS_PATH" --overwrite --profile "$PROFILE"
databricks apps deploy "$APP_NAME" --source-code-path "$WS_PATH" --profile "$PROFILE"
databricks apps get "$APP_NAME" --profile "$PROFILE" -o json | python3 -c "import sys,json; d=json.load(sys.stdin); print('status:', d.get('app_status',{}).get('state'), '\nurl:', d.get('url'))"
