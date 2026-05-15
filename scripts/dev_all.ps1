# dev_all.ps1 — 백엔드 + 프론트엔드 동시 실행 (각각 새 PowerShell 창)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path | Split-Path -Parent

Start-Process powershell -ArgumentList "-NoExit", "-File", (Join-Path $root "scripts\dev_backend.ps1")
Start-Sleep -Seconds 1
Start-Process powershell -ArgumentList "-NoExit", "-File", (Join-Path $root "scripts\dev_frontend.ps1")

Write-Host "Backend: http://localhost:8000"
Write-Host "Frontend: http://localhost:5173 (Vite 도입 후)"
