@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion

echo ╔══════════════════════════════════════╗
echo ║     OpenLucid Upgrade Script      ║
echo ╚══════════════════════════════════════╝
echo.

cd /d "%~dp0"

:: ── 1. Backup .env ──
if exist .env (
    if not exist env-backup mkdir env-backup
    set TIMESTAMP=%date:~0,4%%date:~5,2%%date:~8,2%_%time:~0,2%%time:~3,2%%time:~6,2%
    set TIMESTAMP=!TIMESTAMP: =0!
    copy .env "env-backup\.env.backup_!TIMESTAMP!" >nul
    echo [OK] Backed up .env
) else (
    echo [!!] No .env found — skipping backup
)

:: ── 2. Pull latest code ──
echo Pulling latest code...
git -C .. fetch origin
if %errorlevel% neq 0 (
    echo [FAIL] git fetch failed.
    pause
    exit /b 1
)
git -C .. reset --hard origin/main
echo [OK] Code updated

:: ── 3. Sync new config variables ──
if exist .env if exist .env.example (
    set ADDED=0
    for /f "usebackq tokens=1,* delims==" %%a in (".env.example") do (
        set "KEY=%%a"
        if not "!KEY:~0,1!"=="#" if not "!KEY!"=="" (
            findstr /b /c:"%%a=" .env >nul 2>&1
            if !errorlevel! neq 0 (
                echo %%a=%%b>> .env
                set /a ADDED+=1
                echo   + Added new variable: %%a
            )
        )
    )
    if !ADDED! equ 0 (
        echo [OK] No new config variables
    ) else (
        echo [OK] Added !ADDED! new variable(s) to .env
    )
)

:: ── 4. Ensure Docker Hub connectivity ──
set DOCKER_MIRROR=https://docker.1ms.run
set BASE_IMAGE=python:3.11-slim

echo Pulling latest base image...
docker pull %BASE_IMAGE% >nul 2>&1
if %errorlevel% neq 0 (
    echo [!!] Cannot pull %BASE_IMAGE%. Checking mirror config...
    set "DAEMON_JSON=%USERPROFILE%\.docker\daemon.json"
    findstr /c:"registry-mirrors" "!DAEMON_JSON!" >nul 2>&1
    if !errorlevel! neq 0 (
        echo Configuring Docker Hub mirror: %DOCKER_MIRROR%
        powershell -Command ^
            "if (!(Test-Path '%USERPROFILE%\.docker')) { New-Item -ItemType Directory -Path '%USERPROFILE%\.docker' -Force | Out-Null }; " ^
            "$cfg = if (Test-Path '!DAEMON_JSON!') { Get-Content '!DAEMON_JSON!' -Raw | ConvertFrom-Json } else { [PSCustomObject]@{} }; " ^
            "$cfg | Add-Member -NotePropertyName 'registry-mirrors' -NotePropertyValue @('%DOCKER_MIRROR%') -Force; " ^
            "$cfg | ConvertTo-Json -Depth 10 | Set-Content '!DAEMON_JSON!' -Encoding UTF8"
        taskkill /f /im "Docker Desktop.exe" >nul 2>&1
        timeout /t 3 /nobreak >nul
        start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe" 2>nul
        set /a dw=0
        :wait_docker_upgrade
        if !dw! geq 60 (
            echo [!!] Docker did not restart. Build may use cache.
            goto do_build
        )
        timeout /t 3 /nobreak >nul
        set /a dw+=3
        docker info >nul 2>&1
        if !errorlevel! neq 0 goto wait_docker_upgrade
        echo [OK] Docker mirror configured
        docker pull %BASE_IMAGE% >nul 2>&1
    ) else (
        echo [!!] Mirror already configured. Build will use cache if available.
    )
) else (
    echo [OK] Base image updated
)

:do_build
:: ── 5. Rebuild ──
echo Building images (this may take a moment)...
docker compose build
if %errorlevel% neq 0 (
    echo [FAIL] Build failed.
    pause
    exit /b 1
)
echo [OK] Images rebuilt

:: ── 6. Restart ──
echo Restarting services...
docker compose up -d
echo [OK] Services restarted

:: ── 7. Read port ──
set APP_PORT=80
for /f "tokens=1,2 delims==" %%a in (.env) do (
    if "%%a"=="APP_PORT" set APP_PORT=%%b
)
set APP_PORT=%APP_PORT: =%

echo.
echo ╔══════════════════════════════════════╗
echo ║        Upgrade complete!             ║
echo ╚══════════════════════════════════════╝
echo.
if "%APP_PORT%"=="80" (
    echo   Access OpenLucid at: http://localhost
) else (
    echo   Access OpenLucid at: http://localhost:%APP_PORT%
)
echo   View logs: docker compose logs -f app
echo.
pause
