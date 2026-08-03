@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Configure Notion Local MCP Easy
if not exist ".venv\Scripts\python.exe" (
    where py >nul 2>&1
    if errorlevel 1 (python -m venv .venv) else (py -3 -m venv .venv)
)
echo.
echo This sets up a work area: folder, access mode and which connection profile it uses.
echo Connection profiles themselves are built in PROFILES.bat.
echo START.bat then only asks which work area to run.
echo.
".venv\Scripts\python.exe" launcher.py --setup
pause
