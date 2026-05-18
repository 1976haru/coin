# API Rate Limit Guard (체크리스트 #26)

> Agent Trader Crypto OS v1 — 거래소별 API 호출 폭주 방지 + 재시도 정책

## 0. 한 줄 요약

`RateLimitGuard` (`backend/app/brokers/rate_limit_guard.py`) 는 거래소 호출 전
**`can_call/acquire` 판단**과 호출 후 **응답 헤더/에러 반영 + 재시도 폭주 방지**
를 담당하는 안전장치다. 본 모듈 자체는 HTTP 호출이나 sleep 을 *수행하지 않는다* —
정책 결정만 한다. 실제 sleep 은 caller 가 결정 (테스트는 fake sleep 주입).

## 1. 목적과 범위

본 단계는 다음을 한다.

- 거래소·그룹 별 `RateLimitPolicy` config 화 (upbit / okx / binance / mock / paper).
- 응답 헤더/에러 코드 파싱 통합 — `Remaining-Req`, `X-MBX-USED-WEIGHT(-1M)`,
  OKX `code=50011`, `Retry-After`.
- `RateLimitGuard` — 토큰 버킷 + safety_buffer + cooldown 통합.
- `RetryDecision` — 429/418/50011 발생 시 cooldown + exponential backoff, 무한
  재시도 방지 (`max_retries`).
- `ExchangeRateLimitRegistry` — 거래소·그룹 별 guard 싱글톤 관리.
- REST: `GET /api/rate-limits` — 현재 정책/상태 조회.

본 단계는 다음을 **하지 않는다**.

- 실제 거래소 주문 API 호출 추가 ❌
- 실제 private endpoint 호출 추가 ❌
- LIVE mode 활성화 ❌
- 무한 재시도 / recursive retry ❌
- 실제 sleep 호출 (caller 가 결정) ❌
- 27번 이후 작업 ❌

**운영 전 공식 문서 재확인 필수.** 거래소 한도 정책은 변동 가능하다.

## 2. 모듈 구조

| 파일 | 역할 |
|---|---|
| `app/brokers/api_limits.py` | `RateLimitPolicy` + 거래소별 preset + header/error parser |
| `app/brokers/rate_limit_guard.py` | `RateLimitGuard` + `GuardState` + `AcquireDecision` + `RetryDecision` + `ExchangeRateLimitRegistry` |
| `app/brokers/rate_limiter.py` | (기존) 저수준 `TokenBucket` — guard 가 내부적으로 사용 |
| `app/api/rate_limits.py` | `GET /api/rate-limits` |

## 3. RateLimitPolicy

```python
@dataclass(frozen=True)
class RateLimitPolicy:
    exchange: str
    group: str
    capacity: float
    refill_rate_per_sec: float
    safety_buffer: int = 1
    unit: str = "req"                        # "req" | "weight"
    cooldown_on_429_sec: float = 5.0
    cooldown_on_418_sec: float = 60.0
    cooldown_on_okx_50011_sec: float = 3.0
    cooldown_on_network_sec: float = 0.5
    max_retries: int = 2
    base_backoff_sec: float = 0.5
    max_backoff_sec: float = 8.0
    disabled: bool = False
    notes: str = ""
```

## 4. 거래소별 기본 정책 (preset)

운영 전 거래소 공식 문서에서 한도를 재확인 후 조정하라.

| Exchange | Group | capacity | refill/s | unit | safety_buffer | disabled | 메모 |
|---|---|---|---|---|---|---|---|
| upbit | quotation | 10 | 10 | req | 1 | False | Remaining-Req 헤더로 동적 갱신 |
| upbit | exchange | 8 | 8 | req | 1 | **True** | private/order — 본 단계 disabled |
| okx | public | 20 | 20 | req | 2 | False | 50011 = rate-limit |
| okx | private | 10 | 10 | req | 1 | **True** | disabled |
| okx | trade | 10 | 10 | req | 1 | **True** | disabled |
| binance | spot_public | 1200 | 20 | weight | 240 | False | 1200 weight/min, 80% soft limit |
| binance | spot_private | 10 | 10 | req | 2 | **True** | regulatory review 전 disabled |
| binance | futures | 10 | 10 | req | 2 | **True** | #67 Futures Scope 까지 disabled |
| mock | default | 10000 | 10000 | req | 0 | False | 테스트 |
| paper | default | 10000 | 10000 | req | 0 | False | 테스트 |

알 수 없는 거래소·그룹 → **default conservative** (`capacity=5, refill_rate=5/s,
safety_buffer=1`).

## 5. Header / Error parser

| 함수 | 파싱 대상 | 결과 |
|---|---|---|
| `parse_upbit_remaining_req(header)` | Upbit `Remaining-Req` | `{"group", "min", "sec"}` |
| `parse_okx_error(payload, status_code)` | OKX `{"code", "msg", "data"}` | `{"code", "msg", "is_rate_limit"}` 또는 None |
| `parse_binance_used_weight(headers)` | `X-MBX-USED-WEIGHT(-1M)` + `X-MBX-ORDER-COUNT-*` | `{"used_weight_1m", "order_count_*"}` |
| `parse_retry_after(headers)` | `Retry-After` | float 초 또는 None |

깨진 헤더/payload 는 안전하게 빈 dict / None 반환. 본 함수들은 기존 거래소별
모듈의 parser 를 그대로 재export 해 **backward-compatible** 하다.

## 6. RateLimitGuard

### 6.1 공개 메서드

| 메서드 | 동작 |
|---|---|
| `can_call(weight=1)` → `AcquireDecision` | peek (토큰 소비 없음). disabled / cooldown / safety_buffer 검사 |
| `acquire(weight=1)` → `AcquireDecision` | 토큰 소비. 실패 시 `total_throttled++` |
| `update_from_response(headers, status_code, body)` | 정상 응답 헤더 → state.remaining/used_weight. OKX 50011 body 자동 감지 → cooldown |
| `update_from_error(kind, headers)` → `RetryDecision` | 429/418/50011/network/auth/invalid 분기. Retry-After 헤더 우선 적용 |
| `reset_retry()` | 성공 후 retry 카운터 초기화 |
| `snapshot()` → dict | 정책/상태 read-only (secret 없음) |
| `reset()` | state + bucket 초기화 |

### 6.2 `AcquireDecision`

```python
{
  "allowed": bool,
  "reason": str,                # "ok" / "cooldown active" / "insufficient tokens" / "group disabled"
  "wait_seconds": float,        # caller 가 sleep 시 사용
  "remaining_tokens": float,
  "cooldown_remaining": float,
}
```

### 6.3 `RetryDecision`

```python
{
  "should_retry": bool,
  "wait_seconds": float,        # backoff or cooldown (큰 쪽)
  "reason": str,
  "attempt": int,
  "max_retries": int,
  "cooldown_until": float,
}
```

### 6.4 에러 종류

| 토큰 | 트리거 | retry 가능 |
|---|---|---|
| `rate_limit_429` (별칭: `429`, `rate_limit`) | HTTP 429 / Upbit too many | ✓ (max_retries 까지) |
| `ip_banned_418` (별칭: `418`, `ip_ban`) | HTTP 418 (Binance) | ✓ |
| `okx_50011` (별칭: `50011`, `okx_rate_limit`) | OKX `code=50011` | ✓ |
| `upbit_too_many_requests` | Upbit 429 / 잔여 0 | ✓ |
| `network` (별칭: `timeout`, `connection`) | 네트워크 오류 | ✓ (짧은 backoff) |
| `auth` (별칭: `401`, `403`) | 인증 실패 | ✗ |
| `invalid` (별칭: `400`, `404`) | 잘못된 요청 | ✗ |
| `unknown` | 그 외 | ✓ (보수적) |

### 6.5 재시도 폭주 방지

- `max_retries=2` (기본). 초과 시 `should_retry=False, reason="max retries exceeded"`.
- exponential backoff: `min(max_backoff_sec, base_backoff_sec * 2^attempt)`.
- cooldown 이 더 길면 cooldown 우선.
- `Retry-After` 헤더가 있으면 그 값을 cooldown 으로 사용.
- 본 모듈은 `time.sleep` 을 호출하지 않는다 — caller 가 `RetryDecision.wait_seconds`
  만큼 외부에서 sleep.
- `while True:` / recursive retry 없음 (정적 회귀로 강제).

## 7. ExchangeRateLimitRegistry

거래소·그룹 별 guard 싱글톤 보관.

```python
from app.brokers import build_default_registry

reg = build_default_registry(preload=True)
guard = reg.get("upbit", "quotation")  # 자동 생성 + 캐시
guard.acquire()
# ... HTTP 호출 ...
guard.update_from_response(headers=response.headers, status_code=response.status_code)
```

`build_default_registry(preload=True)` 는 `_DEFAULT_POLICIES` 의 모든 정책을 미리
등록한다. 운영자가 `reg.register(custom_policy)` 로 커스텀 정책 주입 가능.

## 8. REST API

### `GET /api/rate-limits`

```jsonc
{
  "guards": [
    {
      "exchange": "upbit", "group": "quotation",
      "disabled": false,
      "capacity": 10, "refill_rate_per_sec": 10.0, "unit": "req",
      "safety_buffer": 1,
      "remaining_tokens": 10.0,
      "remaining_header": null,
      "used_weight": null,
      "cooldown_remaining_sec": 0.0,
      "consecutive_failures": 0,
      "last_error_code": "",
      "current_retry_attempt": 0,
      "max_retries": 2,
      "stats": {
        "total_calls": 0, "total_acquired": 0, "total_throttled": 0,
        "total_429": 0, "total_418": 0, "total_okx_50011": 0,
        "total_network_errors": 0,
        "total_retries_issued": 0, "total_retries_denied": 0
      },
      "policy_notes": "..."
    },
    // ... 다른 guard
  ],
  "count": 10,
  "updated_at": "2026-05-18T...",
  "warning": "Rate-limit policies are conservative defaults — verify against exchange documentation before live operations."
}
```

응답에 secret/token/api_key 가 포함되지 않는다 (정적 회귀로 강제).

reset endpoint 는 본 단계에서 추가하지 않는다 (운영자 실수 방지).

## 9. 사용 예 (adapter 연결 패턴)

본 단계에서는 adapter 에 자동 연결을 *강제하지 않는다* — 기존 adapter 테스트가
깨지지 않게 보존. 실제 production 호출 시 권장 패턴:

```python
from app.brokers import build_default_registry, ERROR_KIND_429
import time

reg = build_default_registry()

def fetch_with_guard(exchange: str, group: str, do_request):
    guard = reg.get(exchange, group)
    dec = guard.acquire()
    if not dec.allowed:
        # caller 가 sleep 결정
        time.sleep(dec.wait_seconds)
        dec = guard.acquire()
        if not dec.allowed:
            raise RuntimeError(f"rate-limited: {dec.reason}")
    try:
        resp = do_request()
        guard.update_from_response(
            headers=resp.headers, status_code=resp.status_code,
            body=resp.json() if hasattr(resp, "json") else None,
        )
        guard.reset_retry()
        return resp
    except RateLimitError as e:
        rd = guard.update_from_error(ERROR_KIND_429, headers=e.headers)
        if not rd.should_retry:
            raise
        time.sleep(rd.wait_seconds)
        return fetch_with_guard(exchange, group, do_request)  # NOTE: 실제로는
        # while 루프로 — recursive 는 피한다. 본 예시는 단순화.
```

운영 코드는 **recursive 대신 for-loop + `max_retries`** 사용 권장.

## 10. 안전 정책 요약

- 본 단계 완료는 실거래 허가가 아니다 (CLAUDE.md §2.6).
- `RateLimitGuard` 는 호출 결정만 한다 — 실제 HTTP / sleep 은 caller 책임.
- 본 모듈은 `requests` / `httpx` / `ccxt` / `pyupbit` / `binance` / `okx` SDK 를
  import 하지 않는다 (정적 회귀).
- `while True` / recursive retry 부재 (정적 회귀).
- private/order/futures group 은 모두 `disabled=True` — 본 guard 가 호출 자체를
  차단.
- API 응답에 secret/token 부재 (정적 회귀).
- Strategy/Agent 가 guard 모듈을 직접 import 하지 않는다 (compliance.py meta-checker
  만 예외).

## 11. 회귀 테스트

`backend/tests/test_rate_limit_guard.py` — **53 케이스**. 분류:

1. **Policy validation** (3) — capacity/unit/max_retries
2. **Default policies** (4) — known/unknown + disabled flags (okx/binance/upbit)
3. **`parse_retry_after`** (4)
4. **`parse_okx_error`** (4) — ok / 50011 / status 429 / other code
5. **`parse_binance_used_weight`** (2)
6. **`parse_upbit_remaining_req`** (1)
7. **`can_call`** — 기본 / disabled / safety_buffer (3)
8. **cooldown 차단** (1)
9. **`acquire`** + 카운터 (2)
10. **`update_from_response`** — Upbit / Binance / OKX 50011 자동 감지 (3)
11. **`update_from_error`** — 429 / 418 / 50011 / no-retry / 별칭 (5)
12. **max_retries / 무한 루프 방지** (2)
13. **exponential backoff + cap** (1)
14. **Retry-After 우선** (1)
15. **reset** (1)
16. **snapshot 키 + secret 부재** (1)
17. **Registry** — get / unknown / register / snapshot_all / preload (5)
18. **REST API** — `/api/rate-limits` 응답 + secret 부재 (2)
19. **정적 회귀** — 금지 문자열 / SDK import / __all__ exports / Strategy·Agent
    import 부재 (5)

```
cd backend
python -m pytest tests/test_rate_limit_guard.py -q
```

## 12. 후속 단계

- adapter 자동 연결 — 후속 PR. 본 단계는 guard 만 제공하고 운영자가 명시적으로 연결.
- DB 영속 — guard 상태를 audit_events 에 기록 (후속 PR).
- LIVE 활성화 — 별도 LIVE adapter + OrderGateway 끝단 + 별도 환경변수 + 별도 승인
  절차 통과 후에만.
- 27번 Secret Permissions / 28번 Sandbox-Paper Keys — 본 작업 범위 밖.
