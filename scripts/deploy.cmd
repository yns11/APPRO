@echo off
rem Enveloppe pour les postes Windows dont la stratégie d'exécution PowerShell interdit les .ps1 :
rem   scripts\deploy.cmd dev PROD --var="sync_role=prenom.nom@exemple.com"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy.ps1" %*
exit /b %errorlevel%
