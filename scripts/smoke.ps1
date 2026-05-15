# smoke.ps1 — 백엔드가 떠 있는 상태에서 핵심 엔드포인트 응답 확인
$ErrorActionPreference = "Stop"
$base = if ($env:SMOKE_BASE_URL) { $env:SMOKE_BASE_URL } else { "http://localhost:8000" }

Write-Host "Smoke against $base"

function Hit([string]$path, [string]$method = "GET", $body = $null) {
    $uri = "$base$path"
    try {
        if ($method -eq "GET") {
            $r = Invoke-RestMethod -Uri $uri -Method GET -TimeoutSec 5
        } else {
            $r = Invoke-RestMethod -Uri $uri -Method $method -Body ($body | ConvertTo-Json) -ContentType "application/json" -TimeoutSec 5
        }
        Write-Host "[OK]    $method $path"
        return $r
    } catch {
        Write-Host "[FAIL]  $method $path  ->  $($_.Exception.Message)"
        throw
    }
}

$status = Hit "/api/status"
if ($status.enable_live_trading -ne $false) { throw "ENABLE_LIVE_TRADING 이 false 가 아님" }
if ($status.enable_ai_execution -ne $false) { throw "ENABLE_AI_EXECUTION 이 false 가 아님" }

Hit "/api/freshness" | Out-Null
Hit "/api/strategies/kimp/signal" "POST" @{
    symbol = "BTC"; upbit_price_krw = 138000000; okx_price_usdt = 100000; usdt_krw = 1380
} | Out-Null

Write-Host "OK — smoke passed"
