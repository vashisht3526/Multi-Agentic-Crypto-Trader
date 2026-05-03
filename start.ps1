# ================================================
# Chronos Platform - START
# Launches backend (pipeline + API) and frontend
# ================================================

$ErrorActionPreference = "SilentlyContinue"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host ""
Write-Host "  ========================================" -ForegroundColor Cyan
Write-Host "   Chronos - Temporal Signal OS" -ForegroundColor Cyan
Write-Host "  ========================================" -ForegroundColor Cyan
Write-Host ""

# Step 1: Kill any existing instances
Write-Host "[1/4] Cleaning up old processes..." -ForegroundColor Yellow

# Kill python processes running main.py or uvicorn
Get-Process python* -ErrorAction SilentlyContinue | ForEach-Object {
    $cmdline = (Get-CimInstance Win32_Process -Filter "ProcessId=$($_.Id)" -ErrorAction SilentlyContinue).CommandLine
    if ($cmdline -match "main\.py" -or $cmdline -match "uvicorn" -or $cmdline -match "backend\.api") {
        Write-Host "  Killing python PID $($_.Id)" -ForegroundColor DarkGray
        Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
    }
}

# Kill node processes running vite
Get-Process node* -ErrorAction SilentlyContinue | ForEach-Object {
    $cmdline = (Get-CimInstance Win32_Process -Filter "ProcessId=$($_.Id)" -ErrorAction SilentlyContinue).CommandLine
    if ($cmdline -match "vite") {
        Write-Host "  Killing vite PID $($_.Id)" -ForegroundColor DarkGray
        Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
    }
}

Start-Sleep -Seconds 2

# Step 2: Check venv exists
Write-Host "[2/4] Checking virtual environment..." -ForegroundColor Yellow
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    Write-Host "  ERROR: Virtual environment not found at .venv\" -ForegroundColor Red
    Write-Host "  Run: python -m venv .venv && .\.venv\Scripts\pip install -r requirements.txt" -ForegroundColor Red
    exit 1
}
Write-Host "  Venv OK: $VenvPython" -ForegroundColor DarkGray

# Check frontend node_modules
$NodeModules = Join-Path $ProjectRoot "frontend\node_modules"
if (-not (Test-Path $NodeModules)) {
    Write-Host "  Installing frontend dependencies..." -ForegroundColor Yellow
    Push-Location (Join-Path $ProjectRoot "frontend")
    npm install 2>&1 | Out-Null
    Pop-Location
}

# Step 3: Start Backend
Write-Host "[3/4] Starting Backend (Pipeline + FastAPI on port 8000)..." -ForegroundColor Yellow
$backendProcess = Start-Process -FilePath $VenvPython `
    -ArgumentList "main.py" `
    -WorkingDirectory $ProjectRoot `
    -WindowStyle Minimized `
    -PassThru

# Save PID for stop script
$backendProcess.Id | Out-File (Join-Path $ProjectRoot ".backend.pid") -Force
Write-Host "  Backend PID: $($backendProcess.Id)" -ForegroundColor DarkGray

Start-Sleep -Seconds 3

# Step 4: Start Frontend
Write-Host "[4/4] Starting Frontend (Vite dev server on port 5173)..." -ForegroundColor Yellow
$frontendProcess = Start-Process -FilePath "cmd.exe" `
    -ArgumentList "/c cd /d `"$(Join-Path $ProjectRoot 'frontend')`" && npm run dev" `
    -WindowStyle Minimized `
    -PassThru

$frontendProcess.Id | Out-File (Join-Path $ProjectRoot ".frontend.pid") -Force
Write-Host "  Frontend PID: $($frontendProcess.Id)" -ForegroundColor DarkGray

Start-Sleep -Seconds 2

Write-Host ""
Write-Host "  ========================================" -ForegroundColor Green
Write-Host "   Platform is running!" -ForegroundColor Green
Write-Host "  ----------------------------------------" -ForegroundColor DarkGray
Write-Host "   Backend API:  http://localhost:8000" -ForegroundColor White
Write-Host "   Frontend UI:  http://localhost:5173" -ForegroundColor White
Write-Host "  ----------------------------------------" -ForegroundColor DarkGray
Write-Host "   To stop: .\stop.ps1" -ForegroundColor Yellow
Write-Host "  ========================================" -ForegroundColor Green
Write-Host ""

# Open browser
Start-Process "http://localhost:5173"
