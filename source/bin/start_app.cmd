@echo off
REM ==================================================
REM  FILE: start_app.cmd
REM ==================================================
REM  NAS2GP start-up launcher for Windows.
REM  - Ensures PYTHONPATH includes the source folder
REM  - Uses system Python or activated .venv python
REM  - Switches code page to 932 (SJIS) for stable console output
REM ==================================================

setlocal

chcp 932 >NUL

set APP_HOME=%~dp0..
cd /d "%APP_HOME%"

set PYTHONPATH=%APP_HOME%;%PYTHONPATH%

if exist "%APP_HOME%\.venv\Scripts\python.exe" (
    "%APP_HOME%\.venv\Scripts\python.exe" "%APP_HOME%\app.py"
) else (
    python.exe "%APP_HOME%\app.py"
)

set EXITCODE=%ERRORLEVEL%

endlocal & exit /b %EXITCODE%
