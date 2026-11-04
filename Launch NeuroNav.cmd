@echo off
setlocal
cd /d "%~dp0"
set "NAV_PYTHON=%TEMP%\neuro-nav-01a18756-env\Scripts\python.exe"
if not exist "%NAV_PYTHON%" (
  echo The NeuroNav runtime was not found. See README.md for setup.
  pause
  exit /b 1
)
"%NAV_PYTHON%" app.py %*
if errorlevel 1 pause
