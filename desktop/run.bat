@echo off
REM Quick-launch script for development (no build required)
cd /d "%~dp0\.."

if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
)

REM Bundle official Android platform-tools on first run if adb is unavailable.
if not exist "tools\platform-tools\adb.exe" (
    where adb >nul 2>&1
    if errorlevel 1 (
        echo adb not found; fetching official Android platform-tools...
        "%PY%" setup_adb.py
    )
)

"%PY%" -m desktop.main %*