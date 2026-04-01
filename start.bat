@echo off
:: ================================================
:: CryptoSignals Platform - START
:: Launches backend (pipeline + API) and frontend
:: ================================================

echo.
echo  ========================================
echo   CryptoSignals - AI Trading Platform
echo  ========================================
echo.

:: Kill any existing instances first
echo [1/3] Cleaning up old processes...
taskkill /F /FI "WINDOWTITLE eq CryptoBackend" >nul 2>&1
for /f "tokens=2" %%a in ('tasklist /FI "IMAGENAME eq node.exe" /FO LIST 2^>nul ^| findstr "PID:"') do (
    wmic process where "ProcessId=%%a" get CommandLine 2>nul | findstr "vite" >nul && taskkill /PID %%a /F >nul 2>&1
)

:: Delete stale DuckDB lock files
if exist ".\binance_ticks.duckdb.wal" (
    echo   Cleaning stale WAL file...
)

:: Start Backend
echo [2/3] Starting Backend (Pipeline + FastAPI on port 8000)...
start "CryptoBackend" /MIN cmd /c "cd /d %~dp0 && .\.venv\Scripts\python.exe main.py 2>&1 | findstr /V /C:"NativeCommandError""
timeout /t 3 /nobreak >nul

:: Start Frontend
echo [3/3] Starting Frontend (Vite dev server on port 5173)...
start "CryptoFrontend" /MIN cmd /c "cd /d %~dp0\frontend && npm run dev"
timeout /t 2 /nobreak >nul

echo.
echo  ========================================
echo   Platform is running!
echo  ----------------------------------------
echo   Backend API:  http://localhost:8000
echo   Frontend UI:  http://localhost:5173
echo  ----------------------------------------
echo   To stop: run stop.bat
echo  ========================================
echo.

:: Open browser
start http://localhost:5173
