#!/usr/bin/env bash
# Contrôle de livraison attesté (audit F32) : tout ce qu'un contributeur exécute avant de pousser, en une commande,
# avec un relevé horodaté (commit, résultats) écrit dans docs/validation.log.
#
#   scripts/check.sh            # ruff + pytest + lint + typecheck + build (+ parcours Playwright si APPRO_CHECK_E2E=1)
#
# La CI reste déclenchée à la main (.github/workflows/ci.yml) : ce script est le contrôle minimal exigé avant un déploiement.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$ROOT/docs/validation.log"
SHA="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo "sans-git")"
STAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
status=0
results=()

run() {   # run <libellé> <commande…>
  local label="$1"; shift
  echo "==> $label"
  if "$@"; then results+=("OK   $label"); else results+=("FAIL $label"); status=1; fi
}

run "ruff (backend, scripts, jobs)" bash -c "cd '$ROOT/backend' && ruff check appro ../scripts ../jobs"
run "pytest (backend)" bash -c "cd '$ROOT/backend' && python -m pytest -q"
run "eslint (client)" bash -c "cd '$ROOT/client' && npm run --silent lint"
run "tsc + vite build (client)" bash -c "cd '$ROOT/client' && npm run --silent build"
if [ "${APPRO_CHECK_E2E:-0}" = "1" ]; then
  run "parcours Playwright (client/e2e/smoke.mjs)" bash -c "cd '$ROOT/client' && npm run --silent e2e"
fi

{
  echo "## $STAMP · commit $SHA · $(whoami 2>/dev/null || echo ?)"
  for r in "${results[@]}"; do echo "- $r"; done
  echo
} >> "$LOG"
echo
printf '%s\n' "${results[@]}"
echo "relevé ajouté à docs/validation.log (commit $SHA)"
exit $status
