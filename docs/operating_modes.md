# Operating Modes — Agent Trader Crypto OS v1

> 체크리스트 **#3** — 6단계 운용 모드와 capability matrix.
> 단일 진리 소스: `app/core/modes.py` (TradingMode enum + ModeCapability + allowed_transitions).
> 본 문서가 코드와 어긋나면 코드가 옳다. 회귀 테스트가 어긋남을 막는다.

---

## 1. 6단계 모드

| # | 모드 | 한 줄 설명 | 기본 노출 |
|---:|---|---|---|
| 1 | `SIMULATION` | 백테스트/리플레이 (시계열 재생) | ✅ |
| 2 | `PAPER` | 가상 주문 (실시세) | ✅ **(기본값)** |
| 3 | `LIVE_SHADOW` | 실시세, 신호 기록만 (주문 송신 없음) | 옵트인 |
| 4 | `LIVE_MANUAL_APPROVAL` | 사람이 카드 단위로 승인 후 실주문 | 옵트인 |
| 5 | `LIVE_AI_ASSIST` | AI 제안 + 사람 최종 승인 후 실주문 | 옵트인 |
| 6 | `LIVE_AI_EXECUTION` | AI 제한 자동 실행 (옵트인 + 별도 플래그) | 옵트인 |

기본값: `TRADING_MODE=PAPER`. `app/core/modes.py::safe_default_mode()` 가 단일 출처.

---

## 2. Capability Matrix (행동 허용 표)

`app/core/modes.py::ModeCapability` 의 9개 행동에 대한 모드별 허용/금지.
회귀 테스트(`tests/test_mode_capabilities.py`)가 본 표와 코드 일치를 검증.

| 행동 | SIM | PAPER | SHADOW | MANUAL | AI_ASSIST | AI_EXEC |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| `can_emit_signal`           | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `can_run_paper_orders`      | ✅ | ✅ | ⛔ | ⛔ | ⛔ | ⛔ |
| `can_log_shadow`            | ⛔ | ⛔ | ✅ | ✅ | ✅ | ✅ |
| `needs_manual_approval`     | ⛔ | ⛔ | ⛔ | ✅ | ✅ | ⛔ |
| `can_execute_live`          | ⛔ | ⛔ | ⛔ | ✅ | ✅ | ✅ |
| `can_execute_live_ai_auto`  | ⛔ | ⛔ | ⛔ | ⛔ | ⛔ | ✅ * |
| `can_use_kimp_strategy`     | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ * |
| `can_use_futures`           | ⛔ | ⛔ | ⛔ | ⛔ | ⛔ | ⛔ † |
| `requires_admin_token`      | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

\* capability 자체는 true 지만, `ENABLE_AI_EXECUTION` 또는 `ENABLE_KIMP_STRATEGY` 플래그가 별도로 false 면 PermissionGate가 차단.

† `ENABLE_CRYPTO_FUTURES_LIVE` + Phase 8 (#67-72) 완료 전까지 매트릭스 false 고정.

### 2.1 PermissionGate 라우트 결정 (ModeCapability 사용 흐름)

```text
order, source → PermissionGate.check()
  capability = capability_for(mode)

  is_kimp ? → !ENABLE_KIMP_STRATEGY → BLOCKED
  capability.can_run_paper_orders → route="paper"
  capability.can_log_shadow & !can_execute_live → route="shadow"
  capability.needs_manual_approval & ENABLE_LIVE_TRADING → route="approval_queue"
  !ENABLE_LIVE_TRADING → BLOCKED
  capability.can_execute_live_ai_auto & source=="ai" & ENABLE_AI_EXECUTION → route="live"
  default → BLOCKED
```

(현재 `app/risk/permission_gate.py` 는 capability matrix 도입 전 구조이므로, 향후 #52 강화 시 위 흐름으로 리팩터한다. 본 PR에서는 외부 인터페이스 호환을 위해 유지.)

---

## 3. 모드 전환 규칙

### 3.1 승격 (Promote)
```text
SIMULATION → PAPER → LIVE_SHADOW
                  → LIVE_MANUAL_APPROVAL
                  → LIVE_AI_ASSIST
                  → LIVE_AI_EXECUTION
```

- 한 단계만 위로. **건너뛰기 금지**.
- 승격 게이트(`app/governance/promotion_gates.py`) + 사용자 명시 승인 모두 통과 필요.
- 코드 진리 소스: `allowed_transitions(mode)["promote"]`

### 3.2 강등 (Downgrade)
- 어디서든 한 단계 아래로 가능 (사고 대응 / 자동 강등).
- 자동 강등 트리거 (`promotion_gates.check_reversion`):
  - 일 손실 ≤ -3%
  - 연속 오류 > 5
- 코드 진리 소스: `allowed_transitions(mode)["downgrade"]`

### 3.3 비상 정지 (Emergency)
- 모든 모드 → `SIMULATION` 즉시 강등 가능.
- KillSwitch 활성화는 별도 layer (`RiskManager.activate_kill_switch`) — 모드 변경 없이 신규 진입만 차단.
- 코드 진리 소스: `allowed_transitions(mode)["emergency"]` (= `SIMULATION`)

### 3.4 전환 다이어그램

```text
       ┌────────────┐
       │ SIMULATION │ ◀────────────── (emergency from anywhere)
       └────┬───────┘
            │ promote
            ▼
       ┌────────────┐
       │   PAPER    │ ◀── safe_default_mode()
       └────┬───────┘
            │ promote
            ▼
       ┌────────────┐
       │LIVE_SHADOW │
       └────┬───────┘
            │ promote (paper_gate PASS + 사용자 승인)
            ▼
  ┌──────────────────────┐
  │LIVE_MANUAL_APPROVAL  │
  └──────┬───────────────┘
         │ promote
         ▼
  ┌──────────────────────┐
  │   LIVE_AI_ASSIST     │
  └──────┬───────────────┘
         │ promote (별도 게이트 #66 + ENABLE_AI_EXECUTION 옵트인)
         ▼
  ┌──────────────────────┐
  │  LIVE_AI_EXECUTION   │
  └──────────────────────┘

  강등은 위 화살표를 거꾸로 한 단계씩.
```

---

## 4. 모드별 운영 체크리스트

### SIMULATION
- [ ] 데이터 소스가 record/replay 모드인지 확인
- [ ] 결과는 실 손익이 아님을 UI에서 표시

### PAPER (기본)
- [ ] `MAX_ORDER_NOTIONAL_USDT` 등 리스크 파라미터 합리적
- [ ] PaperBroker 슬리피지/수수료 설정 점검
- [ ] AuditLog 가 PAPER_ORDER_FILLED 이벤트 기록

### LIVE_SHADOW
- [ ] `ENABLE_LIVE_TRADING=false` (송신 금지)
- [ ] 시세 read-only API key
- [ ] 매일 SHADOW_SIGNAL_LOGGED 카운트 모니터링

### LIVE_MANUAL_APPROVAL ↑ (LIVE 진입 시 공통)
- [ ] `ENABLE_LIVE_TRADING=true` 명시
- [ ] OKX/Upbit API 키 출금 권한 제거 확인
- [ ] `ADMIN_TOKEN` 강력한 값으로 회전
- [ ] KillSwitch 작동 검증
- [ ] DailyReport 모니터링 인력 배치
- [ ] 사용자 1명이 작업 중일 때만 활성화

### LIVE_AI_EXECUTION
- [ ] `ENABLE_AI_EXECUTION=true` 명시 (별도 플래그)
- [ ] AI Permission Gate 회귀 테스트 모두 통과
- [ ] 일 손실 한도 / 동시 포지션 한도 보수적 설정
- [ ] 사용자 옵트인 문서 보관

---

## 5. 안전 원칙 재확인

- 위험 capability는 **default deny**. 매트릭스에서 명시적 true 일 때만 허용.
- 신규 모드 추가 또는 capability 추가 시:
  1. `ModeCapability` dataclass 필드 추가
  2. 모든 모드에 대한 값 명시 (default 의존 금지)
  3. 본 문서 표 갱신
  4. `tests/test_mode_capabilities.py` 갱신
- 모드 변경은 항상 admin token 요구 (`requires_admin_token=True` 모든 모드).

---

## 6. 변경 이력

| 일자 | 변경 |
|---|---|
| 2026-05-10 | #3 산출물: ModeCapability + capability_for + allowed_transitions 도입. 9개 행동 매트릭스 명시. |
| (이전) | TradingMode enum + property 패턴 (Step A 이전부터). |
