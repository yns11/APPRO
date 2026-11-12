#!/usr/bin/env bash
# =============================================================================
# Vérifie, compile le frontend, valide le bundle, déploie et démarre l'App.
#
#   scripts/deploy.sh <cible> <profil> [--var=...]
#   scripts/deploy.sh dev PROD --var="sync_role=prenom.nom@exemple.com"
#
# `databricks bundle deploy` ne vérifie pas que l'interface a été compilée : un
# déploiement sans client/dist réussit, l'API répond, et il ne manque « que »
# le frontend. Ce script rend l'oubli impossible. Il exécute aussi le
# vérificateur statique (.claude/skills/databricks-livraison) avant de déployer.
# Sous Windows : scripts\deploy.cmd (ou deploy.ps1).
# =============================================================================
set -euo pipefail
racine="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${racine}"
cible="${1:-}"
profil="${2:-}"
if [ -z "${cible}" ] || [ -z "${profil}" ]; then
  echo "Usage : scripts/deploy.sh <cible> <profil> [--var=...]" >&2
  exit 1
fi
shift 2

echo "→ Vérification statique du bundle"
python3 .claude/skills/databricks-livraison/verifier_bundle.py .

bundle="client/dist/index.html"
if [ ! -f "${bundle}" ] || [ -n "$(find client/src client/index.html client/package.json -newer "${bundle}" 2>/dev/null | head -1)" ]; then
  echo "→ Compilation du frontend"
  scripts/build.sh
else
  echo "✓ Interface à jour, compilation ignorée."
fi

echo "→ Validation du bundle (cible : ${cible})"
databricks bundle validate -t "${cible}" --profile "${profil}" "$@"
echo "→ Déploiement (définitions de l'App et du job)"
databricks bundle deploy -t "${cible}" --profile "${profil}" "$@"
echo "→ Démarrage / redémarrage de l'App (un déploiement seul laisse l'App arrêtée)"
databricks bundle run appro -t "${cible}" --profile "${profil}" "$@"
echo "✓ Déployé. État et URL :"
databricks apps get "appro-${cible}" --profile "${profil}" -o json | python3 -c "import sys,json; d=json.load(sys.stdin); print('  état :', d.get('app_status',{}).get('state'), '\n  url  :', d.get('url'))"
