@echo off
rem Double-clickable launcher for Windows.  -ExecutionPolicy Bypass applies to
rem this one process only, so MUedit starts without the machine-wide
rem Set-ExecutionPolicy step that otherwise blocks unsigned local scripts.
setlocal
cd /d "%~dp0"

where uv >nul 2>&1
if errorlevel 1 (
  echo MUedit needs uv, which does not appear to be installed.
  echo.
  echo Open PowerShell, paste this line, let it finish, then close PowerShell
  echo and double-click MUedit again:
  echo.
  echo   powershell -c "irm https://astral.sh/uv/install.ps1 ^| iex"
  echo.
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run_MUedit.ps1"

if errorlevel 1 (
  echo.
  echo MUedit stopped with an error. The messages above say what went wrong.
  pause
)
