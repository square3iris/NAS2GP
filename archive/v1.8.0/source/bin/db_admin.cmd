@echo off
REM ==================================================
REM  FILE: db_admin.cmd
REM ==================================================

setlocal

chcp 932 >NUL

set APP_HOME=%~dp0..
cd /d "%APP_HOME%"

set PYTHONPATH=%APP_HOME%;%PYTHONPATH%

if exist "%APP_HOME%\.venv\Scripts\python.exe" (
    "%APP_HOME%\.venv\Scripts\python.exe" "%APP_HOME%\db_admin.py" %*
) else (
    python.exe "%APP_HOME%\db_admin.py" %*
)

set EXITCODE=%ERRORLEVEL%
endlocal & exit /b %EXITCODE%
