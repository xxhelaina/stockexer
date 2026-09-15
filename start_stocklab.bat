@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel% equ 0 (
    py -3 main.py
) else (
    python main.py
)

if %errorlevel% neq 0 (
    echo.
    echo StockLab failed to start. Install dependencies with:
    echo python -m pip install -r requirements.txt
    echo.
    pause
)

