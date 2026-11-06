#!/usr/bin/env bash
# Compile le frontend (client/dist). À exécuter avant tout déploiement : client/dist n'est pas
# versionné (artefact de build) mais ré-inclus dans la synchronisation du bundle (sync.include).
#   scripts/build.sh            # npm introuvable ? NPM="/c/Program Files/nodejs/npm" scripts/build.sh
set -euo pipefail
cd "$(dirname "$0")/.."
npm_bin="${NPM:-npm}"
"${npm_bin}" --prefix client ci --no-fund --no-audit
"${npm_bin}" --prefix client run build
test -f client/dist/index.html || { echo "Échec : client/dist/index.html absent" >&2; exit 1; }
echo "✓ frontend compilé dans client/dist"
