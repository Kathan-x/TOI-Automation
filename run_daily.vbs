' =====================================================================
' Times of India Daily Downloader - Lightweight Pre-Execution Gate
' =====================================================================
' 1. Determines today's local date (YYYY, MonthName, YYYY-MM-DD).
' 2. Checks whether today's valid newspaper PDF already exists in:
'    Desktop\TOI Daily\YYYY\MonthName\TOI_Ahmedabad_YYYY-MM-DD.pdf
'    or if %LOCALAPPDATA%\TOI-Daily\state.json marks today as completed.
' 3. If today's paper is ALREADY completed:
'    -> Exits IMMEDIATELY in ~2ms.
'    -> NEVER launches run_daily.bat, Python runtime, browser, or network.
' 4. If today's paper is NOT completed:
'    -> Silently launches run_daily.bat in background (window style 0).
' 5. If --force argument is passed, bypasses the gate.
' =====================================================================

Option Explicit

Dim WshShell, FSO, ScriptDir, BatFile, AppData, StateFile
Dim TodayDate, YearStr, MonthStr, DayStr, DateStr, MonthNameStr
Dim DesktopDir, UserProfile, PdfPath, ForceRun, i

Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")

ScriptDir = FSO.GetParentFolderName(WScript.ScriptFullName)
BatFile = ScriptDir & "\run_daily.bat"

' Check if --force was passed
ForceRun = False
If WScript.Arguments.Count > 0 Then
    For i = 0 To WScript.Arguments.Count - 1
        If LCase(WScript.Arguments(i)) = "--force" Then
            ForceRun = True
        End If
    Next
End If

If Not ForceRun Then
    TodayDate = Now
    YearStr = CStr(Year(TodayDate))
    MonthStr = Right("0" & CStr(Month(TodayDate)), 2)
    DayStr = Right("0" & CStr(Day(TodayDate)), 2)
    DateStr = YearStr & "-" & MonthStr & "-" & DayStr
    MonthNameStr = MonthName(Month(TodayDate), False)

    ' 1. Check Standard Desktop & OneDrive Desktop
    DesktopDir = WshShell.SpecialFolders("Desktop")
    UserProfile = WshShell.ExpandEnvironmentStrings("%USERPROFILE%")

    Dim PotentialPaths(2)
    PotentialPaths(0) = DesktopDir & "\TOI Daily\" & YearStr & "\" & MonthNameStr & "\TOI_Ahmedabad_" & DateStr & ".pdf"
    PotentialPaths(1) = UserProfile & "\Desktop\TOI Daily\" & YearStr & "\" & MonthNameStr & "\TOI_Ahmedabad_" & DateStr & ".pdf"
    PotentialPaths(2) = UserProfile & "\OneDrive\Desktop\TOI Daily\" & YearStr & "\" & MonthNameStr & "\TOI_Ahmedabad_" & DateStr & ".pdf"

    Dim pIndex, testPath, pdfFile
    For pIndex = 0 To 2
        testPath = PotentialPaths(pIndex)
        If FSO.FileExists(testPath) Then
            On Error Resume Next
            Set pdfFile = FSO.GetFile(testPath)
            ' A valid TOI edition is > 100 KB
            If Err.Number = 0 And pdfFile.Size > 102400 Then
                ' Today's paper is already completed! Exit immediately without launching Python/network.
                WScript.Quit 0
            End If
            On Error GoTo 0
        End If
    Next

    ' 2. Check state.json in %LOCALAPPDATA%\TOI-Daily\state.json
    AppData = WshShell.ExpandEnvironmentStrings("%LOCALAPPDATA%")
    StateFile = AppData & "\TOI-Daily\state.json"
    If FSO.FileExists(StateFile) Then
        On Error Resume Next
        Dim Stream, JsonText
        Set Stream = FSO.OpenTextFile(StateFile, 1)
        If Err.Number = 0 Then
            JsonText = Stream.ReadAll
            Stream.Close
            If InStr(JsonText, """last_successful_date"": """ & DateStr & """") > 0 And InStr(JsonText, """status"": ""success""") > 0 Then
                ' State verifies today completed. Exit instantly!
                WScript.Quit 0
            End If
        End If
        On Error GoTo 0
    End If
End If

' If today's paper is missing or not completed, launch run_daily.bat hidden (0)
WshShell.Run """" & BatFile & """", 0, False
