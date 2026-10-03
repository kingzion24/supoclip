@echo off
rem Stops Katakata. Your clips, account and settings are kept.
setlocal EnableExtensions
cd /d "%~dp0"
set "COMPOSE=docker compose"
docker compose version >nul 2>&1
if errorlevel 1 set "COMPOSE=docker-compose"
set "KATAKATA_OUTPUT_DIR=%CD%"
if exist ".katakata-local" (
    for /f "usebackq tokens=1,* delims==" %%A in (".katakata-local") do (
        if /i "%%A"=="OUTPUT_DIR" set "KATAKATA_OUTPUT_DIR=%%B"
    )
)
echo Stopping Katakata...
%COMPOSE% -f docker-compose.yml -f docker-compose.windows.yml stop
echo Stopped. Run katakata-start.bat to start again.
pause
