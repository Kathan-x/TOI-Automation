' =====================================================================
' Times of India Daily Downloader - Silent Background Launcher
' Runs the downloader quietly without flashing a CMD window on startup
' =====================================================================

Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")

ScriptDir = FSO.GetParentFolderName(WScript.ScriptFullName)
BatFile = ScriptDir & "\run_daily.bat"

' Run hidden (0) and do not wait for return
WshShell.Run """" & BatFile & """", 0, False
