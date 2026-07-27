@echo off
setlocal
set "PLUGIN_DIR=%~dp0."
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 "%~dp0..\..\plugin_setup.py" --plugin-dir "%PLUGIN_DIR%" --action status
) else (
  python "%~dp0..\..\plugin_setup.py" --plugin-dir "%PLUGIN_DIR%" --action status
)
if errorlevel 1 pause
