# Safety Principles — Agent Trader Crypto OS v1

> 체크리스트 **#4** — 안전 원칙의 단일 진리 소스 (사람용 풀어 쓴 버전).
> 같은 원칙을 짧게 적은 작업자용 강제 문서: [`CLAUDE.md`](../CLAUDE.md).
> 본 문서가 어긋나면 **CI 또는 회귀 테스트가 PR을 막는다** (각 규칙에 강제 메커니즘 인용).

---

## 1. 무엇이 위험한가 (이 시스템이 막아야 하는 사고)

작년 이노그릿(역김프 봇) 실패와 일반 자동매매 사고 사례에서 도출:

| 사고 유형 | 결과 | 본 시스템의 대응 |
|---|---|---|
| AI/전략이 직접 거래소 호출 → 의도치 않은 폭주 주문 | 단시간 대량 손실 | 단일 주문 경로 + import 경계 회귀 테스트 |
| API 키 출금 권한 활성 → 키 탈취 시 자산 유출 | 전손 가능 | `ENABLE_WITHDRAWAL` 영구 false, 출금 함수 미구현 |
| `.env` / API key git 커밋 | 키 누출 | `.gitignore` + `security-ci.yml` secret 스캔 |
| frontend 에 API 키/admin 토큰 번들 | XSS/조회로 누출 | 정책 + grep + GitHub Pages는 mock-only |
| 데이터 지연 중 신규 진입 → 잘못된 가격으로 체결 | 슬리피지 손실 | Freshness 가드 (#16) + Permission/Risk 단계 차단 |
| AI 자동 실행 권한 점진 확대 누적 → 사람 모르는 사이 LIVE_AI_EXECUTION | 전략 오류가 즉시 손실 | 6단계 모드 + 사용자 명시 옵트인 + 정량 게이트 |
| 한 번 PASS한 전략을 영구 신뢰 → 알파 감쇠 미감지 | 누적 손실 | 자동 강등 (`promotion_gates.check_reversion`) + AlphaDecay (#94) |
| 감사 로그 누락/수정 → 사고 재현 불가 | 책임 추적 실패 | AuditLog 영속 + 삭제 금지 정책 |

---

## 2. 절대 금지 (Hard Rules)

### 2.1 실거래/주문/출금
1. **실제 거래소 LIVE order 호출 금지** (별도 PR + 별도 환경변수 + 사용자 승인 전).
2. **출금 권한이 부여된 API Key 사용 금지**. `ENABLE_WITHDRAWAL` 코드상 영구 false (`app/core/feature_flags.py`).
3. 거래소 API 키를 만들 때 **출금 권한 체크박스 해제** 필수. 운영 가이드: `docs/api_key_policy.md` (작성 예정 #27).

### 2.2 시크릿/커밋
4. API Key, Secret, Passphrase, Telegram Token, 계좌번호, PII **커밋 금지**.
5. `.env` 커밋 금지 (`.gitignore` + `security-ci.yml::Forbid .env in repo`).
6. `.env.example` 은 **변수명만**, 값은 비워둔다.
7. frontend 번들에 어떤 secret도 포함 금지. localStorage/sessionStorage 에도 저장 금지.

### 2.3 배포 격리
8. **GitHub Pages 는 mock/demo UI 전용**. 실제 backend 데이터 흐름 금지 (#79).
9. 실제 backend 는 local 또는 private 환경 (Tailscale 등 사설망). 공개 인터넷 노출 금지.
10. 실거래 키와 모의/테스트 키는 **완전히 분리** (#28).

### 2.4 격리 폴더
11. `backend/_legacy_innogrit/` 는 **참고 전용**. 활성 코드에서 import 금지.
12. 격리 폴더는 Python 패키지가 아니다 (`__init__.py` 없음).

---

## 3. AI Agent 안전 원칙 (제품 차원 핵심)

### 3.1 역할 한계
- AI Agent 는 **분석·추천·설명만**. 직접 주문 금지.
- 주문 의도가 있는 AgentDecision 도 가능하지만, 반드시 OrderGateway 경로를 통과해야 함.
- `AgentDecision.is_order_intent` 기본값 `false`. true 로 설정해도 **PermissionGate 가 모드/플래그 검증**.

### 3.2 import 경계
- AI Agent (`app/agents/*`) 는 다음을 **import 금지**:
  - `app.brokers.*` (어떤 BrokerAdapter 도 직접 부르면 안 됨)
  - `app.execution.paper_executor`, `app.execution.shadow_executor` (실행기 직접 호출 금지)
  - `app.execution.order_gateway` (Gateway 자체도 Agent 가 직접 부르지 않음 — 라우터/주문 후보 큐를 거침)
- `ExecutionRecommender` (#42) 도 예외 없음. 주문 후보를 ApprovalQueue 로 넘길 뿐, 실행은 사람/시스템 책임.

### 3.3 RiskOfficerAgent 거부권
- `RiskOfficerAgent` (#40) 가 **REJECT** 반환 시 어떤 주문 후보도 생성되지 않는다.
- 낮은 confidence 판단은 `WATCH_ONLY` (사용자에게 보여주되 액션 없음).

### 3.4 강제 메커니즘
- 회귀 테스트: `tests/test_api_smoke.py::test_agents_do_not_import_brokers`
- AgentOrchestrator: `MIN_QUALITY_SCORE=70` 미달 자동 HOLD (`app/agents/orchestrator.py`)

---

## 4. 단일 주문 경로 (Single Order Path)

**모든 주문성 결정**은 다음 경로만 통과한다. 우회는 PR 거절 사유.

```text
StrategySignal (signal-only, is_order_intent=false 기본)
   │
   ▼
AgentReview (AgentOrchestrator → AgentDecision)
   │  품질 < 70 → HOLD
   │  Anomaly veto → HOLD
   │  RiskOfficerAgent REJECT → HOLD
   │
   ▼
RiskManager.evaluate(order, account, freshness_block_reasons)
   │  KillSwitch / Emergency / 일일 손실 / 연속 손실
   │  주문 금액 / 포지션 수 / 레버리지 / 쿨다운 / 데이터 신선도(BUY)
   │
   ▼
OrderGuard (#51)
   │  idempotency / duplicate / cooldown / pending
   │
   ▼
PermissionGate.check(order, source)
   │  capability_for(mode) + ENABLE_* 플래그 결합
   │  route ∈ { paper, shadow, approval_queue, live, blocked }
   │
   ├──→ paper          → PaperExecutor → PaperBroker  → AuditLog
   ├──→ shadow         → AuditLog only (주문 송신 금지)
   ├──→ approval_queue → ApprovalQueue → 사람 승인 후 OrderGateway 재진입
   ├──→ live           → LiveExecutor (의도적 미연결, 별도 PR)
   └──→ blocked        → AuditLog (이유 기록)
                                       │
                                       ▼
                                 AuditLog.record(...)
```

### 4.1 우회 금지 (route_order 원칙)
- Strategy / Agent / Frontend 가 PaperBroker / MockBroker / Upbit·OKX Adapter 의 `place_order` 를 **직접 호출 금지**.
- `app/execution/route_order.py` (#54 OrderExecutor) 가 PermissionGate 의 route 결정에 따라 분기. 외부에서 분기 우회 금지.
- 회귀 테스트: `tests/test_api_smoke.py::test_active_code_does_not_use_old_paths`, `test_strategies_do_not_import_brokers`, `test_agents_do_not_import_brokers`.

### 4.2 SELL/CLOSE 의 비대칭성
- BUY/OPEN 은 freshness/플래그/Approval/RiskGuard **전부** 통과 필요.
- SELL/CLOSE 는 **위험 축소 목적**이므로 freshness 체크 면제 (RiskManager 내부).
- 단, SELL/CLOSE 도 OrderGateway 경로는 그대로 사용. 우회 금지.

---

## 5. 위험 플래그 기본 false

| 플래그 | 의미 | 기본 | 켜는 방법 |
|---|---|:---:|---|
| `ENABLE_LIVE_TRADING` | 실제 주문 송신 | false | 별도 PR + 사용자 명시 옵트인 |
| `ENABLE_AI_EXECUTION` | AI 자동 실행 | false | 위 + AI Assist Gate (#66) PASS |
| `ENABLE_CRYPTO_FUTURES_LIVE` | 선물 실거래 | false | Phase 8 (#67-72) 완료 후 |
| `ENABLE_KIMP_STRATEGY` | 김프/역김프 | true (paper-only) | LIVE 활성은 LIVE_TRADING 과 함께 |
| `ENABLE_LIVE_ORDER_SUBMISSION` | 주문 송신 layer | false | 위 |
| `ENABLE_WITHDRAWAL` | 출금 | **영구 false** | **불가** (코드상 강제) |

새 위험 기능을 도입하면 **항상 새 플래그 + 기본 false**.

회귀 테스트:
- `tests/test_modes_flags.py::test_dangerous_flags_default_false`
- `tests/test_modes_flags.py::test_withdrawal_flag_permanently_false`

---

## 6. 자동 진입 차단 조건 (BUY)

다음 중 하나라도 참이면 **신규 BUY/진입 차단** (RiskManager + PermissionGate):

1. WebSocket reconnecting 중
2. stale data (`FRESHNESS_THRESHOLD_SEC` 초과)
3. quote missing
4. 환율 이상치 (Kimp 전략의 경우)
5. 거래소 공지 위험 (입출금 중단/유의/상폐, #18)
6. KillSwitch 활성
7. 일일 손실 한도 도달
8. 연속 손실 한도 도달
9. 동시 포지션 수 한도
10. 주문 금액 / 레버리지 한도 초과
11. 재진입 쿨다운 중

청산(SELL)은 위험 축소 목적의 별도 정책 (#36 Funding Cost Guard 등은 SELL에도 적용).

---

## 7. 모듈 경계 (Boundaries)

본 시스템은 코드 레벨 경계로 안전 원칙을 강제한다.

| Layer | 금지된 import | 회귀 테스트 |
|---|---|---|
| `app.agents.*` | `app.brokers.*`, `app.execution.paper_executor`, `app.execution.order_gateway` | `test_agents_do_not_import_brokers` |
| `app.strategies.*` | `app.brokers.*`, `app.execution.*` | `test_strategies_do_not_import_brokers` |
| `app.*` (활성 코드) | `_legacy_innogrit.*`, `from utils.`, `import pyupbit`, `import ccxt` | `test_active_code_does_not_import_legacy` |
| `app.*` | `app.storage`, `app.promotion`, `app.market.models`, `app.risk.approval_queue`, `app.execution.paper_broker` (구 경로) | `test_active_code_does_not_use_old_paths` |
| `frontend/*` | secret, API key, admin token (어떤 형태로든) | `security-ci.yml` (grep 패턴) |

### 7.1 frontend secret 금지 (구체 예시)
**금지**:
```ts
// ❌ NO
const ADMIN_TOKEN = "real-admin-token";
localStorage.setItem("api_key", "...");
import.meta.env.VITE_OKX_API_KEY  // 빌드 시 번들에 박힘
```

**허용**:
```ts
// ✅ OK — 사용자 입력 토큰을 fetch 헤더에만 사용, 저장 시 prompt+세션
const token = sessionStorage.getItem("admin_token") ?? prompt("Admin Token:");
fetch("/api/kill-switch", { headers: { "X-Admin-Token": token }, ... });
```

GitHub Pages 배포에서는 `isDemo=true` 분기로 mockData 만 사용 (#79 deployment_github_pages_demo.md).

---

## 8. 승격 정책 (Promotion ≠ Live 허가)

> **체크리스트 PASS 는 실거래 허가가 아니다.**

LIVE 활성화에는 **세 가지 모두** 필요:
1. **정량 기준 PASS** — `app/governance/promotion_gates.py` 의 자동 게이트
2. **체크리스트 P0 항목 완료** — 해당 단계 진입에 필요한 모든 P0 항목
3. **사용자 명시 승인** — 환경변수 변경 + 별도 문서 작성 + 별도 테스트 + 사람 승인

승격 후에도 기준 미달 시 **자동 강등** (`promotion_gates.check_reversion`):
- 일 손실 > 3% → 한 단계 강등
- 연속 오류 > 5회 → 한 단계 강등

긴급 시 **모든 모드 → SIMULATION** 즉시 강등 가능 (`allowed_transitions(mode)["emergency"]`).

---

## 9. 감사 로그 (Audit Log) 정책

- 모든 주문성 결정 (승인/거절/차단/체결/Kill) 은 `AuditLog.record()` 호출 (`app/audit/audit_log.py`).
- 감사 로그는 **삭제 금지**. archive(이동) 만 허용. 향후 #87 에서 redaction 정책 정의 (PII 마스킹).
- 메모리 + CSV 이중 저장. 재시작해도 유지.
- LIVE 사고 시 모든 결정을 재현 가능해야 한다 (이것이 본 시스템의 가장 큰 차별점).

---

## 10. 강제 메커니즘 요약 (Enforcement)

각 규칙의 자동 강제 수단을 한눈에:

| 규칙 | 자동 강제 |
|---|---|
| AI/Strategy 가 broker 직접 import 금지 | `tests/test_api_smoke.py` (모듈 경계 grep) |
| 활성 코드가 격리 폴더 import 금지 | `tests/test_api_smoke.py` + `security-ci.yml` |
| 위험 플래그 기본 false | `tests/test_modes_flags.py` |
| 위험 플래그 default true 변경 | `security-ci.yml::Forbid live flag default change` |
| `.env` 커밋 | `.gitignore` + `security-ci.yml::Forbid .env in repo` |
| Secret 패턴 커밋 | `security-ci.yml::Forbid committed secrets` |
| 모드 capability 매트릭스 정확성 | `tests/test_mode_capabilities.py` |
| Freshness stale BUY 차단 | `tests/test_freshness.py`, `test_order_gateway.py` |
| Admin 라우트 토큰 검증 | `tests/test_api_smoke.py::test_audit_requires_admin_token` |

---

## 11. 운영 시 사용자 자체 점검 체크리스트

LIVE 모드 활성화 직전 (또는 매일 장 시작 전):
- [ ] `.env` 가 git에 들어있지 않다 (`git ls-files .env` 빈 출력)
- [ ] OKX/Upbit API 키에 출금 권한 없음 (거래소 콘솔 스크린샷 보관)
- [ ] `ADMIN_TOKEN` 이 기본값 아니다
- [ ] frontend 빌드 결과 grep으로 secret 패턴 없음
- [ ] `pytest backend/tests/` 전부 통과
- [ ] `/api/status` 응답에 `enable_live_trading=false` (PAPER 운영 시)
- [ ] 감사 로그 마지막 24h 정상

---

## 12. 변경 이력

| 일자 | 변경 |
|---|---|
| 2026-05-10 | #4 산출물: 단일 경로/모듈 경계/강제 메커니즘 표/위험 시나리오/운영 점검 체크리스트 추가 |
| Step A | 초기 스켈레톤 |
