@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Notion Local MCP Easy - tunnel diagnostics

if not exist ".venv\Scripts\python.exe" (
    where py >nul 2>&1
    if errorlevel 1 (python -m venv .venv) else (py -3 -m venv .venv)
)

echo.
echo Shows every running tunnel process and marks the one this launcher owns.
echo Anything marked "orphan" is left over from an earlier run and may still
echo hold a relay port. Pass --cleanup to stop them.
echo.

".venv\Scripts\python.exe" launcher.py --doctor %*
pause
