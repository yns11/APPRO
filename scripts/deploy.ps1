<#
.SYNOPSIS
    Vérifie, compile le frontend si nécessaire, valide le bundle, déploie et démarre l'App APPRO.
.EXAMPLE
    .\scripts\deploy.ps1 dev PROD --var="sync_role=prenom.nom@exemple.com"
.NOTES
    Si la stratégie d'exécution bloque les .ps1 : scripts\deploy.cmd (même arguments).
#>
#Requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string] $Cible,
    [Parameter(Mandatory)][string] $Profil,
    [Parameter(ValueFromRemainingArguments)][string[]] $Variables = @()
)
$ErrorActionPreference = 'Stop'
$racine = Split-Path -Parent $PSScriptRoot
Set-Location $racine

Write-Host '-> Vérification statique du bundle'
python .claude/skills/databricks-livraison/verifier_bundle.py .
if ($LASTEXITCODE -ne 0) { throw 'Anomalies bloquantes dans le bundle.' }

$bundle = Join-Path $racine 'client\dist\index.html'
$recompiler = -not (Test-Path $bundle)
if (-not $recompiler) {
    $ref = (Get-Item $bundle).LastWriteTimeUtc
    $sources = @((Join-Path $racine 'client\src'), (Join-Path $racine 'client\index.html'), (Join-Path $racine 'client\package.json')) | Where-Object { Test-Path $_ }
    $recompiler = [bool](Get-ChildItem $sources -Recurse -File -ErrorAction SilentlyContinue | Where-Object { $_.LastWriteTimeUtc -gt $ref } | Select-Object -First 1)
}
if ($recompiler) {
    Write-Host '-> Compilation du frontend'
    $npm = if ($env:NPM) { $env:NPM } else { 'npm' }
    & $npm --prefix (Join-Path $racine 'client') ci --no-fund --no-audit
    if ($LASTEXITCODE -ne 0) { throw 'npm ci a échoué' }
    & $npm --prefix (Join-Path $racine 'client') run build
    if ($LASTEXITCODE -ne 0) { throw 'npm run build a échoué' }
} else {
    Write-Host 'OK Interface à jour, compilation ignorée.'
}

Write-Host "-> Validation du bundle (cible : $Cible)"
databricks bundle validate -t $Cible --profile $Profil @Variables
if ($LASTEXITCODE -ne 0) { throw 'bundle validate a échoué' }
Write-Host '-> Déploiement'
databricks bundle deploy -t $Cible --profile $Profil @Variables
if ($LASTEXITCODE -ne 0) { throw 'bundle deploy a échoué' }
Write-Host "-> Démarrage de l'App"
databricks bundle run appro -t $Cible --profile $Profil @Variables
if ($LASTEXITCODE -ne 0) { throw 'bundle run appro a échoué' }
Write-Host 'OK Déployé. État :'
databricks apps get "appro-$Cible" --profile $Profil -o json
