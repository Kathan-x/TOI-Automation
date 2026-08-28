@echo off
setlocal

echo ======================================================================
echo   Times of India Daily Downloader - Uninstaller
echo ======================================================================
echo.

echo Removing Windows Scheduled Task...
powershell -Command "Unregister-ScheduledTask -TaskName 'TOI_Daily_Downloader' -Confirm:$false -ErrorAction SilentlyContinue" >nul 2>&1
schtasks /Delete /TN "TOI_Daily_Downloader" /F >nul 2>&1
echo [SUCCESS] Scheduled task removed from Windows Task Scheduler.

echo.
echo Removing Windows Startup folder shortcut...
set "STARTUP_SHORTCUT=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\TOI_Daily.lnk"
if exist "%STARTUP_SHORTCUT%" (
    del "%STARTUP_SHORTCUT%" >nul 2>&1
    echo [SUCCESS] Removed Windows Startup folder shortcut.
)

echo.
echo Removing Desktop manual shortcut...
set "DESKTOP_SHORTCUT=%USERPROFILE%\Desktop\Download Today's TOI.lnk"
if exist "%DESKTOP_SHORTCUT%" (
    del "%DESKTOP_SHORTCUT%" >nul 2>&1
    echo [SUCCESS] Removed Desktop shortcut.
)
set "ONEDRIVE_DESKTOP_SHORTCUT=%USERPROFILE%\OneDrive\Desktop\Download Today's TOI.lnk"
if exist "%ONEDRIVE_DESKTOP_SHORTCUT%" (
    del "%ONEDRIVE_DESKTOP_SHORTCUT%" >nul 2>&1
    echo [SUCCESS] Removed OneDrive Desktop shortcut.
)

echo.
echo ======================================================================
echo   Uninstallation Complete.
echo   Automatic daily downloads have been disabled.
echo   Your saved newspapers in Desktop\TOI Daily remain safely preserved.
echo ======================================================================
echo.
pause
