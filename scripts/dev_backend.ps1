# dev_backend.ps1 — 백엔드 개발 서버 시작 (PowerShell)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path | Split-Path -Parent
Set-Location (Join-Path $root "backend")

# 안전 가드 — 위험 플래그 환경에 누출되어 있으면 경고
foreach ($flag in @("ENABLE_LIVE_TRADING","ENABLE_AI_EXECUTION","ENABLE_LIVE_ORDER_SUBMISSION")) {
    $val = [Environment]::GetEnvironmentVariable($flag)
    if ($val -and $val.ToLower() -in @("1","true","yes","on")) {
        Write-Warning "$flag=$val 가 설정되어 있습니다. 의도된 것이 맞나요?"
    }
}

if (-not $env:TRADING_MODE) { $env:TRADING_MODE = "PAPER" }
if (-not $env:ADMIN_TOKEN) { $env:ADMIN_TOKEN = "change-me-local-only" }

Write-Host "TRADING_MODE=$($env:TRADING_MODE)"
Write-Host "Starting uvicorn on http://localhost:8000 ..."
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
