# test_backend.ps1 — 백엔드 pytest 실행
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path | Split-Path -Parent
Set-Location (Join-Path $root "backend")

# 테스트 환경: 위험 플래그 모두 false 보장
$env:TRADING_MODE = "PAPER"
$env:ENABLE_LIVE_TRADING = "false"
$env:ENABLE_AI_EXECUTION = "false"
$env:ADMIN_TOKEN = "test-token"

python -m pytest tests/ -v --tb=short
