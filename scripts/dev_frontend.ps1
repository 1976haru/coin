# dev_frontend.ps1 — 프론트엔드 dev 서버 (placeholder)
# 체크리스트 #7 Vite/React 마이그레이션 후 본 스크립트가 npm run dev 를 호출.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path | Split-Path -Parent
$front = Join-Path $root "frontend"

if (Test-Path (Join-Path $front "package.json")) {
    Set-Location $front
    npm install
    npm run dev
} else {
    Write-Host "Vite/React frontend 미작업 (체크리스트 #7). 단일 HTML 데모는 백엔드 / 경로에서 자동 서빙."
    Write-Host "백엔드만 실행: .\\scripts\\dev_backend.ps1"
}
