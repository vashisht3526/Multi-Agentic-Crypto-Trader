# ================================================
# Chronos Platform - STOP
# Kills all backend and frontend processes
# ================================================

$ErrorActionPreference = "SilentlyContinue"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host ""
Write-Host "  Stopping Chronos Platform..." -ForegroundColor Yellow
Write-Host ""

# Step 1: Kill by saved PIDs
$backendPidFile = Join-Path $ProjectRoot ".backend.pid"
$frontendPidFile = Join-Path $ProjectRoot ".frontend.pid"

if (Test-Path $backendPidFile) {
    $pid = Get-Content $backendPidFile -ErrorAction SilentlyContinue
    if ($pid) {
        Write-Host "  [1/3] Stopping Backend (PID $pid)..." -ForegroundColor Yellow
        # Kill the process tree (parent + children)
        Get-CimInstance Win32_Process -Filter "ParentProcessId=$pid" -ErrorAction SilentlyContinue | ForEach-Object {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }
        Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
    }
    Remove-Item $backendPidFile -Force -ErrorAction SilentlyContinue
}

if (Test-Path $frontendPidFile) {
    $pid = Get-Content $frontendPidFile -ErrorAction SilentlyContinue
    if ($pid) {
        Write-Host "  [2/3] Stopping Frontend (PID $pid)..." -ForegroundColor Yellow
        Get-CimInstance Win32_Process -Filter "ParentProcessId=$pid" -ErrorAction SilentlyContinue | ForEach-Object {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }
        Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
    }
    Remove-Item $frontendPidFile -Force -ErrorAction SilentlyContinue
}

# Step 2: Safety net — kill any remaining python/node processes for this project
Write-Host "  [3/3] Cleanup remaining processes..." -ForegroundColor Yellow

Get-Process python* -ErrorAction SilentlyContinue | ForEach-Object {
    $cmdline = (Get-CimInstance Win32_Process -Filter "ProcessId=$($_.Id)" -ErrorAction SilentlyContinue).CommandLine
    if ($cmdline -match "main\.py" -or $cmdline -match "uvicorn" -or $cmdline -match "backend\.api") {
        Write-Host "    Killing python PID $($_.Id)" -ForegroundColor DarkGray
        Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
    }
}

Get-Process node* -ErrorAction SilentlyContinue | ForEach-Object {
    $cmdline = (Get-CimInstance Win32_Process -Filter "ProcessId=$($_.Id)" -ErrorAction SilentlyContinue).CommandLine
    if ($cmdline -match "vite") {
        Write-Host "    Killing vite PID $($_.Id)" -ForegroundColor DarkGray
        Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
    }
}

Start-Sleep -Seconds 1

Write-Host ""
Write-Host "  ========================================" -ForegroundColor Green
Write-Host "   All processes stopped." -ForegroundColor Green
Write-Host "   DB lock released. Safe to restart." -ForegroundColor Green
Write-Host "  ========================================" -ForegroundColor Green
Write-Host ""
