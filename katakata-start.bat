@echo off
rem ============================================================
rem  Katakata - start on Windows with Docker Desktop
rem
rem  1. Clone the repo, copy .env.example to .env and fill in your keys.
rem  2. Double-click this file (or run: katakata-start.bat).
rem     Run "katakata-start.bat /choose" to pick a different clips folder.
rem ============================================================
setlocal EnableExtensions
cd /d "%~dp0"
title Katakata

echo.
echo  ==========================================
echo    Katakata - AI video clipping
echo  ==========================================
echo.

rem ---------- 1. Docker installed? ----------
where docker >nul 2>&1
if errorlevel 1 (
    echo [X] Docker is not installed.
    echo     Install Docker Desktop for Windows, then run this file again:
    echo     https://www.docker.com/products/docker-desktop/
    start "" "https://www.docker.com/products/docker-desktop/"
    goto :fail
)

rem ---------- 2. Docker running? Start Docker Desktop if not ----------
docker info >nul 2>&1
if not errorlevel 1 goto :docker_ready
echo [..] Docker is not running. Starting Docker Desktop...
if exist "%ProgramFiles%\Docker\Docker\Docker Desktop.exe" (
    start "" "%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
) else (
    echo     Could not find Docker Desktop. Start it from the Start menu.
)
set /a DOCKER_WAIT=0
:wait_docker
timeout /t 5 /nobreak >nul
docker info >nul 2>&1
if not errorlevel 1 goto :docker_ready
set /a DOCKER_WAIT+=5
if %DOCKER_WAIT% geq 240 (
    echo [X] Docker did not start within 4 minutes.
    echo     Open Docker Desktop, wait until it says "Engine running", then run this file again.
    goto :fail
)
echo     still waiting for Docker... %DOCKER_WAIT%s
goto :wait_docker
:docker_ready
echo [OK] Docker is running.

rem ---------- 3. Docker Compose v2 or v1 ----------
set "COMPOSE=docker compose"
docker compose version >nul 2>&1
if errorlevel 1 (
    where docker-compose >nul 2>&1
    if errorlevel 1 (
        echo [X] Docker Compose was not found. Update Docker Desktop.
        goto :fail
    )
    set "COMPOSE=docker-compose"
)

rem ---------- 4. .env file ----------
if not exist ".env" (
    echo [X] No .env file found.
    if exist ".env.example" (
        copy /y ".env.example" ".env" >nul
        echo     Created .env from .env.example and opened it in Notepad.
        echo     Fill in your API keys, save the file, then run this file again.
        start "" notepad ".env"
    )
    goto :fail
)

set "FRONTEND_PORT="
set "ASSEMBLY_AI_API_KEY="
set "TRANSCRIPTION_PROVIDER="
set "LLM="
set "HAS_LLM_KEY="
for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do call :read_env "%%A" "%%B"

if not defined TRANSCRIPTION_PROVIDER set "TRANSCRIPTION_PROVIDER=assemblyai"
if /i "%TRANSCRIPTION_PROVIDER%"=="assemblyai" if not defined ASSEMBLY_AI_API_KEY (
    echo [X] ASSEMBLY_AI_API_KEY is empty in .env. Transcription needs it.
    echo     Add your key, or set TRANSCRIPTION_PROVIDER=whisper to transcribe locally.
    start "" notepad ".env"
    goto :fail
)
if not defined HAS_LLM_KEY (
    echo [X] No AI model key found in .env.
    echo     Set GOOGLE_API_KEY ^(free tier^), OPENAI_API_KEY, ANTHROPIC_API_KEY or
    echo     OPENROUTER_API_KEY, or LLM=ollama:^<model^> for a local model.
    start "" notepad ".env"
    goto :fail
)
if not defined FRONTEND_PORT set "FRONTEND_PORT=3001"
echo [OK] .env looks good.

rem ---------- 5. Clips folder ----------
set "OUT_DIR="
if /i not "%~1"=="/choose" if exist ".katakata-local" (
    for /f "usebackq tokens=1,* delims==" %%A in (".katakata-local") do (
        if /i "%%A"=="OUTPUT_DIR" set "OUT_DIR=%%B"
    )
)
if defined OUT_DIR if not exist "%OUT_DIR%\" set "OUT_DIR="
if not defined OUT_DIR (
    echo [..] Choose the folder where your clips will be saved...
    for /f "usebackq delims=" %%I in (`powershell -NoProfile -ExecutionPolicy Bypass -STA -File "%~dp0scripts\windows\choose-folder.ps1" "%USERPROFILE%\Videos"`) do set "OUT_DIR=%%I"
)
if not defined OUT_DIR (
    set "OUT_DIR=%USERPROFILE%\Videos\Katakata"
    echo     No folder chosen, using the default.
)
if not exist "%OUT_DIR%\" mkdir "%OUT_DIR%"
if not exist "%OUT_DIR%\" (
    echo [X] Could not create the folder "%OUT_DIR%".
    goto :fail
)
> ".katakata-local" echo OUTPUT_DIR=%OUT_DIR%
set "KATAKATA_OUTPUT_DIR=%OUT_DIR%"
echo [OK] Clips will be saved in: %OUT_DIR%

rem ---------- 6. Build and start ----------
echo.
echo [..] Building and starting Katakata. The first run downloads and builds
echo     everything and can take 10-20 minutes. Later starts take seconds.
echo.
%COMPOSE% -f docker-compose.yml -f docker-compose.windows.yml up -d --build
if errorlevel 1 (
    echo.
    echo [X] Docker could not start Katakata. Scroll up for the error.
    echo     Logs: %COMPOSE% logs backend   /   %COMPOSE% logs worker
    goto :fail
)

rem ---------- 7. Wait until the app answers ----------
echo.
echo [..] Waiting for the backend...
set /a APP_WAIT=0
:wait_backend
curl.exe -s -f -o nul -m 5 http://localhost:8000/health/db
if not errorlevel 1 goto :backend_ready
set /a APP_WAIT+=5
if %APP_WAIT% geq 600 goto :app_timeout
timeout /t 5 /nobreak >nul
goto :wait_backend
:backend_ready
echo [OK] Backend is up.

echo [..] Waiting for the web app (the first page load compiles it)...
:wait_frontend
curl.exe -s -f -o nul -m 60 http://localhost:%FRONTEND_PORT%/
if not errorlevel 1 goto :frontend_ready
set /a APP_WAIT+=5
if %APP_WAIT% geq 900 goto :app_timeout
timeout /t 5 /nobreak >nul
goto :wait_frontend
:frontend_ready

echo.
echo  ==========================================
echo    Katakata is running
echo    App:    http://localhost:%FRONTEND_PORT%
echo    Clips:  %OUT_DIR%
echo    Stop:   katakata-stop.bat
echo  ==========================================
echo.
start "" "http://localhost:%FRONTEND_PORT%"
start "" explorer "%OUT_DIR%"
pause
exit /b 0

:app_timeout
echo [X] Katakata did not answer in time.
echo     Check the logs: %COMPOSE% logs --tail 50 backend frontend worker
goto :fail

:fail
echo.
pause
exit /b 1

rem ---------- helpers ----------
:read_env
set "KEY=%~1"
set "VAL=%~2"
rem Drop surrounding quotes and spaces.
if defined VAL set "VAL=%VAL:"=%"
if defined VAL for /f "tokens=* delims= " %%V in ("%VAL%") do set "VAL=%%V"
if not defined VAL exit /b 0
if /i "%KEY%"=="FRONTEND_PORT" set "FRONTEND_PORT=%VAL%"
if /i "%KEY%"=="ASSEMBLY_AI_API_KEY" set "ASSEMBLY_AI_API_KEY=set"
if /i "%KEY%"=="TRANSCRIPTION_PROVIDER" set "TRANSCRIPTION_PROVIDER=%VAL%"
if /i "%KEY%"=="GOOGLE_API_KEY" set "HAS_LLM_KEY=1"
if /i "%KEY%"=="OPENAI_API_KEY" set "HAS_LLM_KEY=1"
if /i "%KEY%"=="ANTHROPIC_API_KEY" set "HAS_LLM_KEY=1"
if /i "%KEY%"=="OPENROUTER_API_KEY" set "HAS_LLM_KEY=1"
if /i "%KEY%"=="LLM" if /i "%VAL:~0,7%"=="ollama:" set "HAS_LLM_KEY=1"
exit /b 0
