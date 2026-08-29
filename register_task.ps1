# =====================================================================
# Times of India Daily Downloader - Scheduled Task & Shortcut Setup (v2.0)
# Configures Windows Task Scheduler with 6:00 AM Daily Trigger
# (with automatic missed-run catchup when laptop turns on)
# and creates the manual "Download Today's TOI" Desktop Shortcut.
# =====================================================================

$ErrorActionPreference = "Stop"

$TaskName = "TOI_Daily_Downloader"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VbsPath = Join-Path $ScriptDir "run_daily.vbs"
$BatPath = Join-Path $ScriptDir "run_daily.bat"

Write-Host "Registering Windows Scheduled Task: $TaskName..." -ForegroundColor Cyan

# 1. Action: Launch silent VBScript runner
$Action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$VbsPath`"" -WorkingDirectory $ScriptDir

# 2. Trigger: Daily at 6:00 AM ONLY (NO logon trigger)
$TriggerDaily = New-ScheduledTaskTrigger -Daily -At "06:00AM"

# 3. Settings:
# - StartWhenAvailable = $True (If laptop was OFF at 6:00 AM, run catchup once when Windows turns on)
# - AllowStartIfOnBatteries = $True (Runs on laptop battery power)
# - DontStopIfGoingOnBatteries = $True
# - MultipleInstances = IgnoreNew (Prevents concurrent duplicate runs)
$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15) `
    -MultipleInstances IgnoreNew

# 4. Register or Update Task in Windows Task Scheduler
try {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $Action `
        -Trigger $TriggerDaily `
        -Settings $Settings `
        -Description "Automatically downloads the daily Times of India Ahmedabad edition to Desktop\TOI Daily once per day at 6:00 AM." | Out-Null

    Write-Host "[SUCCESS] Task '$TaskName' successfully registered in Windows Task Scheduler!" -ForegroundColor Green
    Write-Host "  - Trigger: Daily at 6:00 AM (with automatic missed-run catchup if laptop was offline/off)" -ForegroundColor Gray
}
catch {
    Write-Warning "PowerShell Task Registration failed: $_. Falling back to schtasks.exe..."
    & schtasks /Create /TN "$TaskName" /TR "wscript.exe `"$VbsPath`"" /SC DAILY /ST 06:00 /F /RL LIMITED | Out-Null
    Write-Host "[SUCCESS] Task registered via schtasks.exe fallback." -ForegroundColor Green
}

# 5. Clean up any legacy Startup folder shortcut to prevent logon triggers
try {
    $StartupDir = [System.Environment]::GetFolderPath('Startup')
    $StartupShortcut = Join-Path $StartupDir "TOI_Daily.lnk"
    if (Test-Path $StartupShortcut) {
        Remove-Item -Path $StartupShortcut -Force -ErrorAction SilentlyContinue
        Write-Host "[SUCCESS] Removed legacy logon shortcut from Windows Startup folder." -ForegroundColor Green
    }
}
catch {
    # Ignore cleanup error
}

# 6. Create Manual Desktop Shortcut: "Download Today's TOI.lnk"
try {
    $DesktopDir = [System.Environment]::GetFolderPath('Desktop')
    $DesktopShortcut = Join-Path $DesktopDir "Download Today's TOI.lnk"
    $WshShell = New-Object -ComObject WScript.Shell
    $Shortcut2 = $WshShell.CreateShortcut($DesktopShortcut)
    $Shortcut2.TargetPath = "wscript.exe"
    $Shortcut2.Arguments = "`"$VbsPath`""
    $Shortcut2.WorkingDirectory = $ScriptDir
    $Shortcut2.Description = "Download Today's Times of India (Ahmedabad Edition)"
    $Shortcut2.IconLocation = "imageres.dll, -1002"
    $Shortcut2.Save()
    Write-Host "[SUCCESS] Manual Desktop shortcut created: 'Download Today''s TOI'" -ForegroundColor Green
}
catch {
    Write-Host "Note: Could not create Desktop shortcut ($_); skipping." -ForegroundColor Gray
}
