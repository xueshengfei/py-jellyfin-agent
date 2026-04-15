@echo off
chcp 65001 >nul 2>&1
title Jellyfin Agent Server (Port 5000)

echo ========================================
echo   Jellyfin Agent - One Click Start
echo ========================================
echo.

cd /d "%~dp0"

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found, please install Python 3.10+
    pause
    exit /b 1
)

:: Check venv
if not exist "venv\Scripts\activate.bat" (
    echo [INFO] Creating virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo [ERROR] Failed to create venv
        pause
        exit /b 1
    )
    echo [INFO] Virtual environment created.
)

:: Activate venv
call venv\Scripts\activate.bat

:: Install dependencies
echo [INFO] Installing dependencies...
pip install -r requirements.txt -q

echo.
echo [INFO] Starting Jellyfin Agent on http://localhost:5000
echo [INFO] Press Ctrl+C to stop
echo.

python main.py

pause
