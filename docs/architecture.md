# Architecture — Agent Trader Crypto OS v1

이 문서는 시스템 전체 구조와 데이터/주문 흐름을 정의한다.
체크리스트 #6 (Backend Skeleton) + #7 (Frontend Skeleton) + #8 (Shared Schemas) 의 산출물.

---

## 1. 레이어 구조

```text
Frontend (PC + Mobile + GitHub Pages Demo)        ← React/Vite PWA (#7)
       │  HTTPS (local/private only)
       ▼
Backend (FastAPI)
   api/         라우터 (health, market, strategies, orders, approvals, risk, logs)
   agents/      Agent Council (분석·추천·설명만)
   strategies/  4대 전략 (signal-only)
   risk/        RiskManager + OrderGuard + PermissionGate
   execution/   ApprovalQueue + OrderGateway + Executor
   governance/  PromotionGate + ChecklistGate
   audit/       AuditLog (모든 결정 기록)
   brokers/     base + paper + mock + upbit + okx ...
                (Strategy/Agent 직접 import 금지)
   market/      freshness + collector + quality + fx
   schemas/     공유 데이터 스키마
   db/          SQLAlchemy + Alembic (#13)
   core/        config + modes + feature_flags + app_info
       │  HTTPS (read-only 우선)
       ▼
External: Upbit / OKX / Binance / News / FX / Telegram
```

---

## 2. 단일 주문 경로 (우회 금지)

```text
StrategySignal (signal-only, is_order_intent=false 기본)
  → AgentReview (AgentOrchestrator → AgentDecision)
       품질 < 70 → HOLD
       RiskOfficerAgent REJECT → HOLD
  → RiskManager.evaluate(order, account, freshness)
       KillSwitch / 일일 손실 / 연속 손실 / 주문 금액 / 포지션 수 / 레버리지 / 쿨다운
  → OrderGuard (#51) — idempotency / duplicate / cooldown / pending
  → PermissionGate.check(order, source)
       route ∈ { paper, shadow, approval_queue, live, blocked }
         paper          → PaperExecutor → PaperBroker
         shadow         → AuditLog only (주문 송신 금지)
         approval_queue → ApprovalQueue → 사람 승인 후 OrderGateway 재진입
         live           → (LiveExecutor 미연결, 별도 PR)
         blocked        → AuditLog 기록만
                        ↓
                   AuditLog.record(...)
```

이 경로를 **우회하는 import 자체가 PR 거절 사유**.
`tests/test_api_smoke.py` 가 회귀 차단.

---

## 3. 모듈 책임

| 모듈 | 책임 | 체크리스트 |
|---|---|---|
| `core/config.py` | 환경변수 → Settings (frozen dataclass) | #9 |
| `core/modes.py` | TradingMode enum + capability matrix | #3 |
| `core/feature_flags.py` | 위험 플래그 모음, 기본 false | #10 |
| `core/app_info.py` | 앱명/버전 (UI/감사 로그 일관성) | #5 |
| `schemas/*` | Ticker/OHLCV/KimpSnapshot/OrderBook 등 불변 데이터 | #8 |
| `market/freshness.py` | 시세 신선도 + WebSocket 재연결 가드 | #16 |
| `market/{collector,quality,fx,notices,kimp}.py` | TBD | #15,#17,#18 |
| `brokers/base.py` | ExchangeAdapter 추상 인터페이스 | #20 |
| `brokers/{paper,mock}_broker.py` | 가상/결정론적 브로커 | #25, #24 |
| `brokers/{upbit,okx,binance}_adapter.py` | 거래소별 구현 (read-only 우선) | #21-23 |
| `strategies/base.py` | StrategyBase + StrategySignal | #29 |
| `strategies/kimp_mean_reversion.py` | 김프/역김프 평균회귀 (특수전략) | #33-35 |
| `strategies/{trend,vol,pair}.py` | 추세/변동성/페어 (현재는 strategies.py 통합) | #30-32 |
| `agents/orchestrator.py` | AgentOrchestrator (결정론 + 선택적 LLM) | #37 |
| `agents/{market_observer,risk_auditor,...}.py` | 개별 Agent | #38-46 |
| `risk/manager.py` | RiskManager (KillSwitch/Loss/Cooldown 등) | #47-50 |
| `risk/permission_gate.py` | route 결정 | #52 |
| `risk/order_guard.py` | idempotency/duplicate/cooldown | #51 |
| `execution/order_gateway.py` | 단일 진입점 | #53 |
| `execution/approval_queue.py` | 사람 승인 큐 | #55 |
| `execution/{paper,shadow,route}_executor.py` | 라우팅별 실행기 | #56-57 |
| `governance/promotion_gates.py` | PAPER/SHADOW/AI Assist Gate | #64-66 |
| `audit/audit_log.py` | 메모리 + CSV 로그 | #11, #87 |
| `db/*` | SQLAlchemy + Alembic | #13 |

---

## 4. 안전 원칙이 코드에 박히는 방식

1. **import 경계** — `tests/test_api_smoke.py` 가 매번 검증
   - agents/* 가 brokers/, execution.{paper,order_gateway} import 금지
   - strategies/* 가 brokers/, execution/* import 금지
   - 활성 코드가 `_legacy_innogrit/` import 금지
   - 활성 코드가 `pyupbit`/`ccxt` 직접 import 금지

2. **기본값 false 회귀** — `tests/test_modes_flags.py`
   - ENABLE_LIVE_TRADING/AI_EXECUTION/CRYPTO_FUTURES_LIVE/LIVE_ORDER_SUBMISSION 기본 false
   - ENABLE_WITHDRAWAL은 환경변수로도 켜지지 않음 (영구 false)

3. **단일 경로 강제** — OrderGateway 외 어떤 코드도 PaperBroker.place_order 직접 호출 안 함

4. **격리 폴더** — `backend/_legacy_innogrit/` 는 패키지 아님 (`__init__.py` 없음)

---

## 5. 데이터 흐름 (시세 → 주문 후보)

```text
WebSocket (Upbit/OKX) → market/collector  → schemas/market.OHLCV
                          ↓
                     market/freshness  → FreshnessStatus
                          ↓
                  strategies/* (signal-only) → StrategySignal
                          ↓
                  agents/orchestrator → AgentDecision
                          ↓
                  execution/order_gateway.submit(...)
```

각 단계가 `audit/audit_log.record(event_type, payload)` 를 남긴다.

---

## 6. 배포

- **개발/운영**: 로컬 PC + Docker Compose (`docker compose up -d`).
- **모바일 관제**: Tailscale + PWA (#76, #81).
- **GitHub Pages**: mock/demo UI 전용 (#79). 실제 backend 배포 금지.

---

## 7. 진화

- Step A (이 PR): 디렉토리 정렬 + 모듈 경계 강제 + 격리. 기능 추가 없음.
- Step B 이후: `docs/checklist_progress.md` 의 P0 항목부터 순서대로.
- Tauri 데스크톱 앱 (#82): 베타 후속.
