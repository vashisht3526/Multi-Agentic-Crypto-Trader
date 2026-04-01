@echo off
:: ================================================
:: CryptoSignals Platform - STOP
:: Kills all backend and frontend processes
:: ================================================

echo.
echo  Stopping CryptoSignals Platform...
echo.

:: Kill backend (python main.py)
echo [1/3] Stopping Backend...
taskkill /F /FI "WINDOWTITLE eq CryptoBackend" >nul 2>&1

:: Kill any python processes holding the DB
for /f "tokens=2 delims=," %%a in ('wmic process where "name='python.exe'" get ProcessId /format:csv 2^>nul ^| findstr /R "[0-9]"') do (
    wmic process where "ProcessId=%%a" get CommandLine 2>nul | findstr "main.py" >nul && (
        echo   Killing python PID %%a
        taskkill /PID %%a /F >nul 2>&1
    )
    wmic process where "ProcessId=%%a" get CommandLine 2>nul | findstr "uvicorn" >nul && (
        echo   Killing uvicorn PID %%a
        taskkill /PID %%a /F >nul 2>&1
    )
)

:: Kill frontend (npm/vite)
echo [2/3] Stopping Frontend...
taskkill /F /FI "WINDOWTITLE eq CryptoFrontend" >nul 2>&1
for /f "tokens=2 delims=," %%a in ('wmic process where "name='node.exe'" get ProcessId /format:csv 2^>nul ^| findstr /R "[0-9]"') do (
    wmic process where "ProcessId=%%a" get CommandLine 2>nul | findstr "vite" >nul && (
        echo   Killing vite PID %%a
        taskkill /PID %%a /F >nul 2>&1
    )
)

:: Clean up lock files
echo [3/3] Releasing DB locks...
timeout /t 1 /nobreak >nul

echo.
echo  ========================================
echo   All processes stopped.
echo   DB lock released. Safe to restart.
echo  ========================================
echo.
