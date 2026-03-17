@echo off
chcp 65001 >nul
title Astro Research Platform

echo ╔══════════════════════════════════════╗
echo ║   Astro Research Platform  v0.1.0    ║
echo ╚══════════════════════════════════════╝
echo.

set PROJECT_DIR=%~dp0
set BACKEND_DIR=%PROJECT_DIR%backend
set FRONTEND_DIR=%PROJECT_DIR%frontend
set VENV_DIR=%BACKEND_DIR%\.venv

:: ── 1. Python venv & deps ──
if not exist "%VENV_DIR%\Scripts\activate.bat" (
    echo [1/3] Creating Python virtual environment...
    python -m venv "%VENV_DIR%"
)
call "%VENV_DIR%\Scripts\activate.bat"

python -c "import fastapi" 2>nul
if errorlevel 1 (
    echo [1/3] Installing core Python dependencies...
    pip install -q fastapi "uvicorn[standard]" pydantic pydantic-settings httpx numpy
)

python -c "import astropy" 2>nul
if errorlevel 1 (
    echo [1/3] Installing astronomy libraries (may take a few minutes)...
    start /b pip install astropy astroquery scipy matplotlib > "%TEMP%\astro-pip.log" 2>&1
)

:: ── 2. Frontend deps ──
echo [2/3] Checking frontend dependencies...
cd /d "%FRONTEND_DIR%"
if not exist "node_modules" (
    call npm install --silent
)

:: ── 3. Create data dir ──
if not exist "%PROJECT_DIR%data\fits" mkdir "%PROJECT_DIR%data\fits"

:: ── 4. Start backend ──
echo [3/3] Starting servers...
echo.

cd /d "%BACKEND_DIR%"
start "Astro-Backend" uvicorn app.main:app --host 127.0.0.1 --port 8000 --log-level info

:: ── 5. Start frontend ──
cd /d "%FRONTEND_DIR%"
start "Astro-Frontend" npx vite --host 127.0.0.1 --port 5173

:: Wait and open browser
timeout /t 3 /nobreak >nul
start http://localhost:5173

echo.
echo ════════════════════════════════════════
echo   App is running!
echo   Frontend:  http://localhost:5173
echo   Backend:   http://localhost:8000
echo   API docs:  http://localhost:8000/docs
echo ════════════════════════════════════════
echo.
echo Close this window to stop the servers.
echo.
pause
taskkill /fi "WINDOWTITLE eq Astro-Backend" /f >nul 2>&1
taskkill /fi "WINDOWTITLE eq Astro-Frontend" /f >nul 2>&1
