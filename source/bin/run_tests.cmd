@echo off
REM ==================================================
REM  FILE: run_tests.cmd
REM ==================================================

setlocal

chcp 932 >NUL

set APP_HOME=%~dp0..
cd /d "%APP_HOME%"

set PYTHONPATH=%APP_HOME%;%PYTHONPATH%

if exist "%APP_HOME%\.venv\Scripts\python.exe" (
    "%APP_HOME%\.venv\Scripts\python.exe" -m pytest -v %*
) else (
    python.exe -m pytest -v %*
)

set EXITCODE=%ERRORLEVEL%
endlocal & exit /b %EXITCODE%
