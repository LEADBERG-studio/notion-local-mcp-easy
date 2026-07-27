@echo off
setlocal
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 "%~dp0..\..\plugin_setup.py" --plugin-dir "%~dp0" --action disable
) else (
  python "%~dp0..\..\plugin_setup.py" --plugin-dir "%~dp0" --action disable
)
if errorlevel 1 pause
