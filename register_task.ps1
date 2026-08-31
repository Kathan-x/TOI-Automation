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

# 2. Triggers:
# - Trigger 1 (Logon): Runs on FIRST Windows logon of the day (checks/downloads immediately)
# - Trigger 2 (Daily Schedule): Daily at 6:00 AM with 30-minute retries across the day (for overnight runs and background retries)
$TriggerLogon = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERNAME"
$TriggerDaily = New-ScheduledTaskTrigger -Daily -At "06:00AM"
$TriggerDaily.Repetition = (New-ScheduledTaskTrigger -Once -At "06:00AM" -RepetitionInterval (New-TimeSpan -Minutes 30) -RepetitionDuration (New-TimeSpan -Hours 18)).Repetition
$TriggerDaily.Repetition.StopAtDurationEnd = $False
$Triggers = @($TriggerLogon, $TriggerDaily)

# 3. Settings:
# - StartWhenAvailable = $True (If laptop was OFF, catch up when laptop turns on)
# - AllowStartIfOnBatteries = $True (Runs on laptop battery power)
# - DontStopIfGoingOnBatteries = $True
# - MultipleInstances = IgnoreNew (Prevents concurrent duplicate runs)
$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15) `
    -MultipleInstances IgnoreNew

$Principal = New-ScheduledTaskPrincipal -UserId "$env:USERNAME" -LogonType Interactive

# 4. Register or Update Task in Windows Task Scheduler
try {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $Action `
        -Trigger $Triggers `
        -Settings $Settings `
        -Principal $Principal `
        -Description "Automatically downloads the daily Times of India Ahmedabad edition on first Windows logon of the day, with daily 6:00 AM schedule and 30-minute retries." | Out-Null

    Write-Host "[SUCCESS] Task '$TaskName' successfully registered in Windows Task Scheduler!" -ForegroundColor Green
    Write-Host "  - Trigger 1: Windows Logon (runs on first login of the day, fast-skips subsequent logins)" -ForegroundColor Gray
    Write-Host "  - Trigger 2: Daily at 6:00 AM with 30-minute retries across the day (for overnight runs & auto-retries)" -ForegroundColor Gray
}
catch {
    Write-Warning "PowerShell Task Registration failed: $_. Falling back to schtasks.exe..."
    & schtasks /Create /TN "$TaskName" /TR "wscript.exe `"$VbsPath`"" /SC ONLOGON /F /RL LIMITED | Out-Null
    & schtasks /Create /TN "${TaskName}_Daily" /TR "wscript.exe `"$VbsPath`"" /SC DAILY /ST 06:00 /RI 30 /DU 18:00 /F /RL LIMITED | Out-Null
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
