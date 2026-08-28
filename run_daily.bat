@echo off
setlocal
cd /d "%~dp0"

:: Use pythonw or python to execute main module
if exist "%LocalAppData%\Programs\Python\Python314\python.exe" (
    "%LocalAppData%\Programs\Python\Python314\python.exe" -m src.main %*
) else if exist "%ProgramFiles%\Python314\python.exe" (
    "%ProgramFiles%\Python314\python.exe" -m src.main %*
) else (
    python -m src.main %*
)
endlocal
