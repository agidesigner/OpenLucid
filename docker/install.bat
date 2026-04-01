@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion

echo ╔═══════════════════════════════════════════════╗
echo ║         OpenLucid - Install                ║
echo ║   Marketing World Model                       ║
echo ║   Your data — found, understood, and used by AI║
echo ║   Interfaces: MCP / Agent / App               ║
echo ╚═══════════════════════════════════════════════╝
echo.

cd /d "%~dp0"

:: ── 1. Check Docker ──
where docker >nul 2>&1
if %errorlevel% equ 0 (
    echo [OK] Docker found.
    docker --version
) else (
    echo [!!] Docker not found.
    echo.
    echo Downloading Docker Desktop installer...
    echo This may take a few minutes...
    powershell -Command "Invoke-WebRequest -Uri 'https://desktop.docker.com/win/main/amd64/Docker%%20Desktop%%20Installer.exe' -OutFile '%TEMP%\DockerInstaller.exe'"
    if not exist "%TEMP%\DockerInstaller.exe" (
        echo [FAIL] Download failed.
        echo Please install Docker Desktop manually:
        echo   https://docs.docker.com/desktop/install/windows-install/
        pause
        exit /b 1
    )
    echo Installing Docker Desktop (this may take a few minutes)...
    start /wait "%TEMP%\DockerInstaller.exe" install --quiet --accept-license
    del "%TEMP%\DockerInstaller.exe" >nul 2>&1
    echo.
    echo Docker Desktop installed.
    echo Please restart your computer, launch Docker Desktop,
    echo then re-run this script.
    pause
    exit /b 0
)

:: ── 2. Check Docker daemon ──
docker info >nul 2>&1
if %errorlevel% neq 0 (
    echo [!!] Docker daemon is not running.
    echo.
    echo Starting Docker Desktop...
    start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe" 2>nul
    echo Waiting for Docker to start (up to 60s)...
    set /a waited=0
    :wait_docker
    if !waited! geq 60 (
        echo [FAIL] Docker did not start in time.
        echo Please start Docker Desktop manually and re-run this script.
        pause
        exit /b 1
    )
    timeout /t 3 /nobreak >nul
    set /a waited+=3
    docker info >nul 2>&1
    if %errorlevel% neq 0 goto wait_docker
    echo [OK] Docker daemon is running.
)

:: ── 3. Check Docker Compose ──
docker compose version >nul 2>&1
if %errorlevel% equ 0 (
    echo [OK] Docker Compose found.
) else (
    echo [FAIL] Docker Compose not available.
    echo Please update Docker Desktop to the latest version.
    echo   https://docs.docker.com/desktop/install/windows-install/
    pause
    exit /b 1
)

:: ── 4. Ensure Docker Hub connectivity (auto-configure mirror if needed) ──
set DOCKER_MIRROR=https://docker.1ms.run
set BASE_IMAGE=python:3.11-slim

docker image inspect %BASE_IMAGE% >nul 2>&1
if %errorlevel% equ 0 (
    echo [OK] Base image %BASE_IMAGE% is cached
    goto skip_pull
)

echo Pulling base image %BASE_IMAGE% ...
docker pull %BASE_IMAGE% >nul 2>&1
if %errorlevel% equ 0 (
    echo [OK] Base image ready
    goto skip_pull
)

echo [!!] Cannot pull %BASE_IMAGE% from Docker Hub. Configuring mirror...
set "DAEMON_JSON=%USERPROFILE%\.docker\daemon.json"
powershell -Command ^
    "if (!(Test-Path '%USERPROFILE%\.docker')) { New-Item -ItemType Directory -Path '%USERPROFILE%\.docker' -Force | Out-Null }; " ^
    "$cfg = if (Test-Path '%DAEMON_JSON%') { Get-Content '%DAEMON_JSON%' -Raw | ConvertFrom-Json } else { [PSCustomObject]@{} }; " ^
    "$cfg | Add-Member -NotePropertyName 'registry-mirrors' -NotePropertyValue @('%DOCKER_MIRROR%') -Force; " ^
    "$cfg | ConvertTo-Json -Depth 10 | Set-Content '%DAEMON_JSON%' -Encoding UTF8"

echo Restarting Docker Desktop to apply mirror...
taskkill /f /im "Docker Desktop.exe" >nul 2>&1
timeout /t 3 /nobreak >nul
start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe" 2>nul
set /a dw=0
:wait_docker_mirror
if !dw! geq 60 (
    echo [FAIL] Docker did not restart in time. Please restart Docker Desktop manually and re-run.
    pause
    exit /b 1
)
timeout /t 3 /nobreak >nul
set /a dw+=3
docker info >nul 2>&1
if %errorlevel% neq 0 goto wait_docker_mirror
echo [OK] Docker mirror configured

echo Retrying pull with mirror...
docker pull %BASE_IMAGE% >nul 2>&1
if %errorlevel% neq 0 (
    echo [FAIL] Still cannot pull %BASE_IMAGE%. Please check your network.
    pause
    exit /b 1
)
echo [OK] Base image ready (via mirror)

:skip_pull

:: ── 5. Setup .env ──
if exist .env (
    echo [OK] Config file .env already exists (keeping current values)
) else (
    copy .env.example .env >nul
    :: Generate a random SECRET_KEY
    for /f "delims=" %%k in ('powershell -Command "[System.Convert]::ToHexString([System.Security.Cryptography.RandomNumberGenerator]::GetBytes(32)).ToLower()"') do (
        powershell -Command "(Get-Content .env) -replace '^SECRET_KEY=.*', 'SECRET_KEY=%%k' | Set-Content .env"
        echo [OK] Generated random SECRET_KEY
    )
    echo [OK] Created .env from template
)

:: ── 6. Start services ──
echo.
echo Starting OpenLucid (this may take a few minutes on first run)...
echo.
docker compose up -d --build
if %errorlevel% neq 0 (
    echo [FAIL] Failed to start services.
    echo Check the error above and try again.
    pause
    exit /b 1
)

:: ── 7. Read port from .env ──
set APP_PORT=80
for /f "tokens=1,2 delims==" %%a in (.env) do (
    if "%%a"=="APP_PORT" set APP_PORT=%%b
)
:: Trim spaces
set APP_PORT=%APP_PORT: =%

:: ── 8. Wait for app to be healthy ──
echo.
echo Waiting for application to start...
set /a waited=0
:wait_app
if !waited! geq 60 (
    echo.
    echo [!!] App is still starting. Check logs with: docker compose logs -f app
    goto done
)
timeout /t 2 /nobreak >nul
set /a waited+=2
curl -sf "http://localhost:%APP_PORT%/health" >nul 2>&1
if %errorlevel% neq 0 goto wait_app
echo.
echo [OK] Application is ready!

:done
echo.
echo ╔═══════════════════════════════════════════════╗
echo ║      Installation complete!                   ║
echo ╚═══════════════════════════════════════════════╝
echo.
if "%APP_PORT%"=="80" (
    echo   Open http://localhost in your browser
) else (
    echo   Open http://localhost:%APP_PORT% in your browser
)
echo.
echo   Next steps:
echo     1. Create your admin account on the setup page
echo     2. Go to Settings to configure your LLM
echo     3. Create your first product and start planning!
echo.
echo   View logs:    docker compose logs -f app
echo   Stop:         docker compose down
echo   Upgrade:      upgrade.bat
echo.
pause
