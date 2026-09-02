@echo off
REM Quick-launch script for development (no build required)
cd /d "%~dp0\.."
.venv\Scripts\python.exe -m desktop.main %*
