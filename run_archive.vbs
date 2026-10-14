' =====================================================================
' Times of India Daily Downloader - Monthly Archiving Silent Runner
' =====================================================================
' Launches run_archive.bat completely silently in background (window style 0).
' =====================================================================

Option Explicit

Dim WshShell, FSO, ScriptDir, BatFile

Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")

ScriptDir = FSO.GetParentFolderName(WScript.ScriptFullName)
BatFile = ScriptDir & "\run_archive.bat"

WshShell.Run """" & BatFile & """", 0, False
