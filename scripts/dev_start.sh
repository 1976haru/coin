#!/bin/bash
# bash 사용자용 진입점. Windows 사용자는 scripts/dev_backend.ps1 사용.
set -e
cd "$(dirname "$0")/.."
[ ! -f .env ] && cp .env.example .env && echo ".env 생성 (값은 비어있음)"

cd backend
pip install -r requirements.txt -q

# 안전 가드 — 위험 플래그 강제 false
export TRADING_MODE="${TRADING_MODE:-PAPER}"
export ENABLE_LIVE_TRADING="${ENABLE_LIVE_TRADING:-false}"
export ENABLE_AI_EXECUTION="${ENABLE_AI_EXECUTION:-false}"
export ADMIN_TOKEN="${ADMIN_TOKEN:-change-me-local-only}"

echo "Tests..."
python -m pytest tests/ -q --tb=short 2>&1 | tail -5

echo "Server: http://localhost:8000"
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
