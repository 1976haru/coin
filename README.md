# Agent Trader Crypto OS v1

> AI Agent 기반 코인 자동매매 **연구·검증·관제 플랫폼**.
> 단순 자동매매 봇이 아니다. 기본은 SIMULATION/PAPER/SHADOW이며 실거래는 별도 승인.

| 영역 | 상태 |
|---|---|
| 정체성 | Agent Trader Crypto OS v1 (체크리스트 #5) |
| 기본 모드 | `TRADING_MODE=PAPER` |
| LIVE 플래그 | 모두 기본 false |
| 96항목 체크리스트 진척도 | `docs/checklist_progress.md` 참조 |

---

## 빠른 시작 (로컬)

```powershell
# 1. 환경변수 — 변수명만 복사, 값은 비워둠
Copy-Item .env.example .env

# 2. 의존성
pip install -r backend/requirements.txt

# 3. 백엔드 실행 (PAPER 모드 기본)
.\scripts\dev_backend.ps1

# 4. 확인
curl http://localhost:8000/api/status
# 또는 브라우저: http://localhost:8000/
```

Docker 사용:

```powershell
docker compose up -d
```

---

## 주요 API

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET  | `/api/status`                  | 시스템/모드/플래그 상태 |
| GET  | `/api/freshness`               | 시세 신선도 |
| POST | `/api/strategies/kimp/signal`  | 역김프 신호 생성 (signal-only) |
| POST | `/api/strategies/trend/signal` | 추세 신호 생성 |
| POST | `/api/order/preview`           | 주문 미리보기 (단일 경로) |
| GET  | `/api/approval/queue`          | 승인 대기 |
| POST | `/api/approval/{id}`           | 승인/거절 (admin) |
| POST | `/api/kill-switch`             | 킬 스위치 (admin) |
| POST | `/api/promotion/paper-gate`    | PAPER → SHADOW 승격 평가 (admin) |
| GET  | `/api/audit`                   | 감사 로그 (admin) |
| GET  | `/api/notices`                 | 거래소 공지 (legacy + 영속 ExchangeNotice, #18) |
| POST | `/api/notices/collect`         | 공지 수집 1회 실행 (admin, mock source 기본, #18) |
| GET  | `/api/notices/context`         | Agent용 read-only notice context (`direct_order_allowed=false`, #18) |
| GET  | `/api/notices/types`           | notice_type / severity 카탈로그 |
| GET  | `/api/theme-signals`           | Trend/News/Theme 정규화 신호 (DB-backed, #19) |
| POST | `/api/theme-signals/collect`   | theme signal 1회 수집 (admin, mock provider 기본, #19) |
| GET  | `/api/theme-signals/context`   | NewsTrendAgent용 read-only theme context (`used_for_order=false`) |
| POST | `/api/theme-signals/filter`    | Watchlist 후보 → `candidate_filter_review_required`/ok 라벨 |
| GET  | `/api/theme-signals/sources`   | source / risk_flag 카탈로그 |

상세는 FastAPI `/docs`.

---

## 운용 모드

```text
SIMULATION → PAPER → LIVE_SHADOW
           → LIVE_MANUAL_APPROVAL
           → LIVE_AI_ASSIST
           → LIVE_AI_EXECUTION (옵트인)
```

기본값: `TRADING_MODE=PAPER`. 모든 LIVE 플래그 `false`. 자세한 매트릭스는 `docs/operating_modes.md`.

---

## 절대 안전 원칙 (요약)

전체 원칙은 [`CLAUDE.md`](./CLAUDE.md) + [`docs/safety_principles.md`](./docs/safety_principles.md).

- 실제 거래소 LIVE order 호출 금지
- 출금 권한 API Key 사용 금지
- API Key/Secret/Token/계좌번호 커밋 금지 (`.env`는 git ignore)
- frontend는 어떤 secret도 보유 금지
- AI Agent는 분석·추천·설명만, 직접 주문 금지
- 주문은 반드시 단일 경로: `Strategy → Agent → RiskManager → OrderGuard → PermissionGate → ApprovalQueue → OrderGateway → Executor`
- 체크리스트 PASS는 실거래 허가가 아님

---

## 디렉토리 구조

```text
cointrade/
├─ README.md
├─ CLAUDE.md                      # 작업 원칙 (Claude/사람 모두 따른다)
├─ .env.example                   # 변수명만, 값 비움
├─ docker-compose.yml
├─ docs/                          # 11개 영역 문서
├─ backend/
│  ├─ app/
│  │  ├─ main.py
│  │  ├─ api/                     # 라우터 (health, market, strategies, orders, approvals, risk, logs)
│  │  ├─ core/                    # config, modes, feature_flags, app_info
│  │  ├─ schemas/                 # 공유 데이터 스키마
│  │  ├─ market/                  # freshness, (collector/quality는 #15-17 진행)
│  │  ├─ brokers/                 # base, paper_broker, mock_broker (upbit/okx는 #21-22)
│  │  ├─ strategies/              # kimp_mean_reversion + 전략 모음 (#29-33에서 분해)
│  │  ├─ agents/                  # orchestrator (개별 agent는 #37-46)
│  │  ├─ risk/                    # manager, permission_gate (order_guard는 #51)
│  │  ├─ execution/               # order_gateway, approval_queue (paper/shadow_executor는 #56,#57)
│  │  ├─ governance/              # promotion_gates (checklist_gate는 후속)
│  │  ├─ audit/                   # audit_log (order_audit/agent_decision_log는 #11 확장)
│  │  └─ db/                      # placeholder (#13에서 모델 정의)
│  ├─ tests/                      # 56개 통과 (Step A 완료)
│  └─ _legacy_innogrit/           # 격리, import 금지
├─ frontend/                      # 단일 HTML 데모 (Vite/React 전환은 #7)
├─ scripts/                       # PowerShell 개발/테스트 스크립트
└─ .github/workflows/             # backend-ci, frontend-ci, security-ci 분리
```

---

## 테스트

```powershell
.\scripts\test_backend.ps1
# 또는
cd backend ; python -m pytest tests/ -v
```

현재 56개 통과 (모듈 경계 회귀 + 위험 플래그 기본 false 검증 포함).

---

## 작업 진행

이 저장소는 [96항목 체크리스트](./docs/checklist_mapping.md)를 번호 순으로 진행한다.
진척도는 [`docs/checklist_progress.md`](./docs/checklist_progress.md).

이전 프로토타입의 코드 매핑은 [`docs/restructure_mapping.md`](./docs/restructure_mapping.md) 참조.
