@echo off
setlocal
cd /d "%~dp0"

:: Use pythonw or python to execute monthly archive manager module
if exist "%LocalAppData%\Programs\Python\Python314\python.exe" (
    "%LocalAppData%\Programs\Python\Python314\python.exe" -m src.archive_manager %*
) else if exist "%ProgramFiles%\Python314\python.exe" (
    "%ProgramFiles%\Python314\python.exe" -m src.archive_manager %*
) else (
    python -m src.archive_manager %*
)
endlocal
