# test_frontend.ps1 — 프론트엔드 테스트 (placeholder)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path | Split-Path -Parent
$front = Join-Path $root "frontend"

if (Test-Path (Join-Path $front "package.json")) {
    Set-Location $front
    npm test
} else {
    Write-Host "Vite/React frontend 미작업 (체크리스트 #7)."
    exit 0
}
