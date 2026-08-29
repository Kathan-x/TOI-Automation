@echo off
setlocal enabledelayedexpansion

echo ======================================================================
echo   Times of India (Ahmedabad Edition) Daily Downloader - Setup
echo ======================================================================
echo.

:: 1. Check Python environment
echo [1/4] Checking Python environment...
python --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Python is not found in PATH!
    echo Please install Python 3.8+ and ensure "Add Python to PATH" is checked.
    pause
    exit /b 1
)
python --version

:: 2. Install requirements
echo.
echo [2/4] Installing required Python dependencies...
python -m pip install -r "%~dp0requirements.txt"
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Failed to install dependencies from requirements.txt.
    pause
    exit /b 1
)

:: 3. Prepare AppData and Desktop directories
echo.
echo [3/4] Initializing directories...
python -c "from src.config import load_config; c = load_config(); print('  AppData directory:', c.app_data_dir); print('  Desktop Archive:', c.download_dir)"

:: 4. Register Windows Scheduled Task with Dual Triggers and Catch-up
echo.
echo [4/4] Configuring automated Windows Task Scheduler...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0register_task.ps1"
if %ERRORLEVEL% NEQ 0 (
    echo [WARNING] PowerShell task registration encountered an issue, but standard fallback was applied.
)

echo.
echo ======================================================================
echo   ONE-TIME SETUP COMPLETE!
echo.
echo   - Target Edition: Times of India (Ahmedabad ONLY)
echo   - Destination: Desktop\TOI Daily\ (Contains ONLY .pdf files)
echo   - Execution: Fully automatic every day at 6:00 AM
echo   - Missed Runs: Automatically catches up if laptop was OFF at 6:00 AM
echo.
echo   You never have to run this manually. It will run silently each day.
echo ======================================================================
echo.
pause
