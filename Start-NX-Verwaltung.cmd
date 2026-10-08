@echo off
setlocal
cd /d "%~dp0server"
if not exist ".venv\Scripts\python.exe" (
  echo Die lokale Python-Umgebung fehlt. Bitte zuerst Install-NX-Verwaltung.ps1 ausfuehren.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m nxserver --settings settings.example.json
pause
