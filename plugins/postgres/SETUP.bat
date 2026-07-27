@echo off
setlocal
set "PLUGIN_DIR=%~dp0."
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 "%~dp0..\..\plugin_setup.py" --plugin-dir "%PLUGIN_DIR%" --action setup
) else (
  python "%~dp0..\..\plugin_setup.py" --plugin-dir "%PLUGIN_DIR%" --action setup
)
if errorlevel 1 pause
