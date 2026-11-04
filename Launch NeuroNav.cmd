@echo off
setlocal
cd /d "%~dp0"
set "NAV_PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%NAV_PYTHON%" (
  echo The NeuroNav runtime was not found in "%~dp0.venv".
  echo Run setup.ps1 in this folder first. See README.md.
  pause
  exit /b 1
)
"%NAV_PYTHON%" app.py %*
if errorlevel 1 pause
