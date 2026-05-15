# Restructure Mapping — INNOGRiT v2 → Agent Trader Crypto OS v1

**작성일:** 2026-05-10
**목적:** 기존 cointrade(=innogrit-v2) 코드를 v6 체크리스트 목표 구조로 정렬하기 위한 분류표.
**원칙:** 무작정 삭제 금지. 활성 경로에서 분리 후 격리(quarantine) 또는 이동.

---

## 분류 정의

- **KEEP**     — 현 위치 그대로 유지
- **MOVE**     — 경로만 이동, 코드 변경 없음 (import만 갱신)
- **REFACTOR** — 이동하면서 분해/통합
- **NEW**      — 신규 작성
- **QUARANTINE** — `backend/_legacy_innogrit/` 로 이동, 활성 import 금지 (참고/연구용)
- **DROP**     — 완전 삭제 (현재 없음)

---

## 1. Backend — 활성 코드

| 현재 경로 | 분류 | 새 경로 | 비고 |
|---|---|---|---|
| `backend/app/main.py` | REFACTOR | `backend/app/main.py` (slim) + `app/api/{health,market,strategies,orders,approvals,risk,logs}.py` | 라우터 분리 |
| `backend/app/core/config.py` | REFACTOR | `app/core/config.py` (Settings 유지) | feature_flags 분리 |
| `backend/app/core/modes.py` | KEEP | `app/core/modes.py` | TradingMode enum |
| `backend/app/market/freshness.py` | KEEP | `app/market/freshness.py` | |
| `backend/app/market/models.py` | MOVE | `app/schemas/market.py` | Ticker/OHLCV/KimpSnapshot/OrderBook 은 데이터 스키마 |
| `backend/app/strategies/kimp_mean_reversion.py` | KEEP | `app/strategies/kimp_mean_reversion.py` | |
| `backend/app/strategies/strategies.py` | KEEP (Step A) → REFACTOR (체크리스트 #29-32 시) | (그대로) | Step A에서는 분해하지 않음 — #30/31/32 항목에서 trend_following.py / volatility_breakout.py / pair_trading.py 로 분해 |
| `backend/app/risk/manager.py` | KEEP | `app/risk/manager.py` | |
| `backend/app/risk/permission_gate.py` | KEEP | `app/risk/permission_gate.py` | |
| `backend/app/risk/approval_queue.py` | MOVE | `app/execution/approval_queue.py` | 목표 구조에서는 execution 하위 |
| `backend/app/execution/order_gateway.py` | KEEP (import만 수정) | `app/execution/order_gateway.py` | |
| `backend/app/execution/paper_broker.py` | MOVE | `app/brokers/paper_broker.py` | 브로커는 brokers/ 폴더 |
| `backend/app/agents/orchestrator.py` | KEEP | `app/agents/orchestrator.py` | |
| `backend/app/storage/audit_log.py` | MOVE | `app/audit/audit_log.py` | |
| `backend/app/promotion/gates.py` | MOVE | `app/governance/promotion_gates.py` | |

## 2. Backend — 신규 (Step A 범위)

| 새 경로 | 분류 | 비고 |
|---|---|---|
| `backend/app/core/feature_flags.py` | NEW | ENABLE_* 플래그 모음 (체크리스트 #10) |
| `backend/app/core/app_info.py` | NEW | 앱 이름/버전 (체크리스트 #5) |
| `backend/app/schemas/__init__.py` | NEW | 스키마 패키지 |
| `backend/app/brokers/__init__.py` | NEW | |
| `backend/app/brokers/base.py` | NEW | ExchangeAdapter 추상 인터페이스 (체크리스트 #20 placeholder) |
| `backend/app/brokers/mock_broker.py` | NEW | 결정론적 mock (체크리스트 #24 placeholder) |
| `backend/app/audit/__init__.py` | NEW | |
| `backend/app/governance/__init__.py` | NEW | |
| `backend/app/db/__init__.py` | NEW | placeholder (#13) |
| `backend/app/api/__init__.py` | NEW | 라우터 등록 |
| `backend/app/api/health.py` | NEW | / + /api/status |
| `backend/app/api/market.py` | NEW | /api/freshness 등 |
| `backend/app/api/strategies.py` | NEW | /api/strategies/* |
| `backend/app/api/orders.py` | NEW | /api/order/preview |
| `backend/app/api/approvals.py` | NEW | /api/approval/* |
| `backend/app/api/risk.py` | NEW | /api/kill-switch + /api/promotion/* |
| `backend/app/api/logs.py` | NEW | /api/audit |
| `backend/tests/test_modes_flags.py` | NEW | 모드/플래그 기본값 false 회귀 테스트 |
| `backend/tests/test_health.py` | NEW | /api/status smoke |
| `backend/tests/test_permission_gate.py` | NEW | PermissionGate 단독 단위 테스트 |
| `backend/tests/test_api_smoke.py` | NEW | TestClient 기반 라우터 등록 검증 |

## 3. Backend — 격리 (QUARANTINE)

`backend/app/utils/`, `backend/app/analysis.py`, `backend/app/position_manager.py`, `backend/app/execution/trade_manager_*.py`, `backend/app/execution/exit_engine.py`, `backend/app/market/websocket_feed.py`, `backend/app/market/quotes_guard.py` 는:

- 활성 경로(`app.main`)에서 **import되지 않음** (확인 완료)
- `pyupbit`, `ccxt.async_support`, `utils.logger` 등 새 구조에 없는 의존성 사용
- `trade_manager_live.py`는 OS 환경변수에서 OKX API 키를 직접 읽고 ccxt 호출 → **안전 원칙 위반 가능성**, 즉시 활성 트리에서 분리

→ `backend/_legacy_innogrit/` 폴더로 일괄 이동, README로 "참고용, import 금지" 명시.

| 현재 경로 | 분류 | 새 경로 |
|---|---|---|
| `backend/app/analysis.py` | QUARANTINE | `backend/_legacy_innogrit/analysis.py` |
| `backend/app/position_manager.py` | QUARANTINE | `backend/_legacy_innogrit/position_manager.py` |
| `backend/app/utils/logger.py` | QUARANTINE | `backend/_legacy_innogrit/utils/logger.py` |
| `backend/app/utils/config_manager.py` | QUARANTINE | `backend/_legacy_innogrit/utils/config_manager.py` |
| `backend/app/utils/notifier.py` | QUARANTINE | `backend/_legacy_innogrit/utils/notifier.py` |
| `backend/app/utils/vwap.py` | QUARANTINE | `backend/_legacy_innogrit/utils/vwap.py` |
| `backend/app/utils/async_utils.py` | QUARANTINE | `backend/_legacy_innogrit/utils/async_utils.py` |
| `backend/app/execution/trade_manager_base.py` | QUARANTINE | `backend/_legacy_innogrit/execution/trade_manager_base.py` |
| `backend/app/execution/trade_manager_demo.py` | QUARANTINE | `backend/_legacy_innogrit/execution/trade_manager_demo.py` |
| `backend/app/execution/trade_manager_live.py` | QUARANTINE ⚠️ | `backend/_legacy_innogrit/execution/trade_manager_live.py` |
| `backend/app/execution/exit_engine.py` | QUARANTINE | `backend/_legacy_innogrit/execution/exit_engine.py` |
| `backend/app/market/websocket_feed.py` | QUARANTINE | `backend/_legacy_innogrit/market/websocket_feed.py` |
| `backend/app/market/quotes_guard.py` | QUARANTINE | `backend/_legacy_innogrit/market/quotes_guard.py` |

격리 후 이 코드의 아이디어는 다음 체크리스트 항목에서 흡수:
- `position_manager.py` → 향후 db/models + execution/ 작업 시 참고
- `websocket_feed.py` → 체크리스트 #15 collector.py + #16 freshness 강화 시 참고
- `quotes_guard.py` → 체크리스트 #17 quality.py 시 참고
- `exit_engine.py` (VWAP) → 체크리스트 #56 PaperTrader 강화 시 참고
- `trade_manager_demo.py` (펀딩비/슬리피지 시뮬) → 체크리스트 #25 PaperBroker 고도화 시 참고
- `trade_manager_live.py` → **활용 금지**. 만약 #27 Secret Permissions 작업 시 권한 분리 사례 참고만.
- `notifier.py` → 체크리스트 #77 Notifications 시 참고
- `analysis.py` → 체크리스트 #43 Daily Report Agent 시 참고

## 4. Frontend

| 현재 경로 | 분류 | 새 경로 | 비고 |
|---|---|---|---|
| `frontend/index.html` | KEEP (Step A) | `frontend/index.html` | 단일 HTML 데모. 체크리스트 #7 (Frontend Skeleton) 작업 시 React/Vite로 교체. Step A에서는 그대로 둔다. |

## 5. 루트/설정/도구

| 현재 경로 | 분류 | 새 경로 | 비고 |
|---|---|---|---|
| `README.md` | REFACTOR | `README.md` | INNOGRiT v2 → Agent Trader Crypto OS v1 정체성 |
| `docs/ARCHITECTURE.md` | REFACTOR | `docs/architecture.md` (소문자) | 새 정체성 반영 |
| `docs/CHECKLIST_MAPPING.md` | REFACTOR | `docs/checklist_mapping.md` (소문자) | 96항목 매핑 |
| `.env.example` | REFACTOR | `.env.example` | 변수 목록 보강 (값은 비워둠) |
| `.gitignore` | KEEP | `.gitignore` | |
| `.pre-commit-config.yaml` | KEEP | `.pre-commit-config.yaml` | |
| `docker-compose.yml` | KEEP | `docker-compose.yml` | Step A에서 변경 없음 |
| `backend/Dockerfile` | KEEP | `backend/Dockerfile` | |
| `backend/requirements.txt` | KEEP | `backend/requirements.txt` | Step A에서 변경 없음 (체크리스트 진행하며 정리) |
| `config/config.json` | QUARANTINE | `backend/_legacy_innogrit/config/config.json` | innogrit 트레이딩 룰. 활성 코드에서 미사용. 격리 후 #14 Watchlist/Universe 등에서 참고. |
| `scripts/dev_start.sh` | KEEP (Step A) → REPLACE (Step A-6) | `scripts/dev_start.sh` + 신규 ps1 | |
| `.github/workflows/ci.yml` | REFACTOR | `.github/workflows/{backend-ci,frontend-ci,security-ci}.yml` | 3개로 분리 |

## 6. 신규 docs (Step A-5/6)

- `CLAUDE.md` (루트)
- `docs/product_scope.md` (#1)
- `docs/safety_principles.md` (#4)
- `docs/operating_modes.md` (#3)
- `docs/strategy_portfolio.md` (#2)
- `docs/data_freshness_policy.md` (#16)
- `docs/deployment_local.md`
- `docs/deployment_mobile_tailscale.md` (#81)
- `docs/deployment_github_pages_demo.md` (#79)
- `docs/runbook.md`
- `docs/checklist_progress.md`

## 7. 신규 scripts (Step A-6)

- `scripts/dev_backend.ps1`
- `scripts/dev_frontend.ps1`
- `scripts/dev_all.ps1`
- `scripts/test_backend.ps1`
- `scripts/test_frontend.ps1`
- `scripts/smoke.ps1`
- `scripts/export_checklist_summary.py`

---

## 실행 순서

1. 신규 디렉토리 + 새 파일 생성 (이 PR)
2. import 갱신 (`order_gateway.py`, `main.py`, 4 tests)
3. `_legacy_innogrit/` 격리 이동
4. 기존 위치 파일 삭제 (audit/governance/brokers/schemas/api 로 옮긴 원본)
5. `pytest backend/tests/ -v` → 전부 통과
6. README/CLAUDE/architecture 갱신
7. checklist_progress.md 작성

## 안전 검증

- 활성 import 그래프에서 ccxt/pyupbit 직접 호출이 사라지는지 확인 (`grep -r 'import ccxt\|import pyupbit' backend/app/`)
- 모든 ENABLE_* 플래그 기본 false 유지 확인
- `.env.example`에 실제 값 없음 확인
- 4개 기존 테스트 + 신규 smoke 테스트 통과 확인
