# Env Profiles + Startup Guard (체크리스트 #28)

> Agent Trader Crypto OS v1 — 실거래 키와 모의/테스트 키 완전 분리

## 0. 한 줄 요약

`AppProfile` (PAPER / SHADOW / LIVE / TEST) 과 `KeyProfile` 을 도입해 환경별로 쓰는
키를 명시적으로 분리한다. **기본은 PAPER.** LIVE 는 명시적 confirmation + 검증 모두
통과해야 startup guard 가 부팅을 허용한다. PAPER profile 에서 LIVE 키가 감지되면
즉시 차단한다. **본 단계 완료는 실거래 허가가 아니다** (CLAUDE.md §2.6).

관련 문서:
- [`docs/api_key_policy.md`](api_key_policy.md) — API 키 권한 정책 (#27)
- [`docs/sandbox_paper_keys.md`](sandbox_paper_keys.md) — AdapterMode ↔ 키 매핑 (1차 #28)

## 1. 목적과 범위

본 단계는 다음을 한다.

- `AppProfile` / `KeyProfile` enum 도입 — PAPER / SHADOW / LIVE / TEST.
- `StartupGuard` (`validate_startup_profile` + `enforce_startup_profile`) — 부팅 전
  profile/키 정합성 검증.
- secret 분류 헬퍼 — `classify_secret_value`, `looks_like_real_secret`, `is_safe_secret`,
  `mask_secret` (#27 재export).
- REST: `GET /api/profile` — read-only snapshot (모든 secret 마스킹).
- `.env.example` 에 `APP_PROFILE` / `KEY_PROFILE` / `LIVE_CONFIRMATION` 등 변수.
- `.gitignore` 에 `.env.live` / `.env.shadow` / `.env.paper` 패턴 추가.

본 단계는 다음을 **하지 않는다**.

- 실제 API key / secret 입력 ❌
- 실제 LIVE 주문 활성화 ❌
- 부팅 자체 차단 (strict mode 옵트인) — 기본은 result 반환 + `/api/profile` 노출.
- 29번 이후 작업 ❌

## 2. 프로파일 정의

### 2.1 AppProfile

| Profile | 의미 | 키 정책 | 실거래 |
|---|---|---|---|
| **PAPER** (기본) | Mock/Paper broker — 시뮬레이션 | secret 불필요. placeholder 허용. **LIVE 키 감지 시 차단** | ❌ |
| **SHADOW** | read-only 실시세 + paper decision 검증 | read-only 키만 제한적. **trade/private LIVE 키 감지 시 차단** | ❌ |
| **LIVE** | 실거래 (게이트 통과 후만) | LIVE 키 필수. KEY_PROFILE=LIVE + LIVE_CONFIRMATION + ENABLE_LIVE_TRADING=true | ⚠️ 게이트 통과 시만 |
| **TEST** | CI/pytest 전용 | fake_/test_/dummy_/leaked- 접두사 값만 허용. **real-looking 키 감지 시 차단** | ❌ |

`TradingMode` (#3) 와의 관계:
- `AppProfile` = 환경/키 프로파일 (어떤 키를 써도 되는가)
- `TradingMode` = 전략 실행 정책 (SIMULATION/PAPER/LIVE_SHADOW/...)
- 둘은 직교지만 정합성 검사 — PAPER profile + TRADING_MODE=LIVE_* 조합은 차단.

`AdapterMode` (#20) 와의 관계:
- `AdapterMode` = adapter 별 동작 등급 (READ_ONLY/PAPER/SANDBOX/LIVE)
- `KeyProfile` = 전역 키 슬롯 분류 (PAPER/SHADOW/LIVE/TEST)
- adapter 가 자기 mode 에 맞지 않는 키를 받으면 #20 의 기존 가드가 raise.

### 2.2 KeyProfile

`APP_PROFILE` 과 별도로 *어떤 키 슬롯을 쓰고 있는지* 명시. LIVE profile 은
`KEY_PROFILE=LIVE` 가 필수.

## 3. StartupGuard 규칙

`validate_startup_profile(env)` 는 다음 위반 규칙을 검사한다. 모든 규칙은 *순수
함수* — env dict 만 입력, 외부 부작용 없음.

### 3.1 공통

| Rule | Severity | 조건 |
|---|---|---|
| `withdrawal_forbidden_in_any_profile` | critical | `ENABLE_WITHDRAWAL=true` (어떤 profile 이든) |

### 3.2 PAPER profile

| Rule | Severity | 조건 |
|---|---|---|
| `paper_profile_has_live_keys` | critical | LIVE secret 변수(UPBIT_ACCESS_KEY 등)에 REAL_LOOKING 값 감지 |
| `paper_profile_enables_live_trading` | critical | `ENABLE_LIVE_TRADING=true` |
| `paper_profile_live_trading_mode` | critical | `TRADING_MODE=LIVE_*` |

### 3.3 SHADOW profile

| Rule | Severity | 조건 |
|---|---|---|
| `shadow_profile_has_trade_keys` | critical | trade LIVE 키 감지 (allow_sandbox_keys_only=false 일 때) |
| `shadow_profile_enables_live_trading` | critical | `ENABLE_LIVE_TRADING=true` |

### 3.4 LIVE profile

| Rule | Severity | 조건 |
|---|---|---|
| `live_profile_requires_enable_flag` | critical | `ENABLE_LIVE_TRADING=false` |
| `live_profile_requires_confirmation` | critical | `LIVE_CONFIRMATION` 미설정 또는 불일치 |
| `live_profile_key_profile_mismatch` | critical | `KEY_PROFILE != LIVE` |
| `live_profile_only_sandbox_keys` | critical | LIVE 키 비어있고 sandbox 키만 채움 |
| `live_profile_with_sandbox_only_flag` | critical | `ALLOW_SANDBOX_KEYS_ONLY=true` |
| `public_env_exposes_secret` | critical | `VITE_*SECRET` / `NEXT_PUBLIC_*SECRET` REAL_LOOKING (REQUIRE_LOCAL_SECRETS 시) |

### 3.5 TEST profile

| Rule | Severity | 조건 |
|---|---|---|
| `test_profile_has_real_looking_keys` | critical | REAL_LOOKING secret 감지 |
| `test_profile_enables_live_trading` | critical | `ENABLE_LIVE_TRADING=true` |

### 3.6 일관성

| Rule | Severity | 조건 |
|---|---|---|
| `paper_profile_live_trading_mode` | critical | PAPER + TRADING_MODE=LIVE_* |
| `live_profile_non_live_trading_mode` | warning | LIVE + TRADING_MODE 가 LIVE_* 가 아님 |

## 4. Secret 분류 (4단계)

`classify_secret_value(value)` 가 반환하는 `SecretClassification`:

| Class | 의미 | 예시 |
|---|---|---|
| `SAFE` | None 또는 빈 문자열 | `None`, `""`, `"   "` |
| `PLACEHOLDER` | 운영자가 채울 자리 표시 | `__SET_IN_LOCAL_ENV_ONLY__`, `PLACEHOLDER`, `YOUR_API_KEY_HERE`, `change-me-local-only` |
| `TEST_LOOKING` | 명시적 fake 값 (접두사) | `fake_xyz`, `test_abc123`, `dummy_secret`, `leaked-key-aaaa` |
| `REAL_LOOKING` | 20자+ 영숫자, 엔트로피 ≥3.5 bits/char | 거래소 API key 형태 |

`looks_like_real_secret(value) == True` 일 때만 startup guard 가 차단한다 —
**안전 쪽 오탐** 허용 (placeholder/test 값으로 false positive 없음).

## 5. Secret Masking

`mask_secret(value)` (#27 에서 재사용):
- `None` / empty → `"<unset>"`
- `PLACEHOLDER` → `"<placeholder>"`
- 짧은 값 → `"***"`
- 긴 값 → `"prefix***suffix"` (원본 비포함)

`StartupGuardResult.masked_env_summary` 가 모든 secret 변수의 마스킹 사본을 노출.
`GET /api/profile` 응답에 그대로 포함되며, 평문 원본은 어디에도 등장하지 않는다.

## 6. `.env.example` 정책

### 6.1 추가 변수 (#28)

```bash
APP_PROFILE=PAPER                      # PAPER | SHADOW | LIVE | TEST
KEY_PROFILE=PAPER                      # same enum
LIVE_CONFIRMATION=                     # LIVE profile 에서만 채움 (정확한 phrase 일치)
REQUIRE_LOCAL_SECRETS=true             # LIVE/SHADOW 에서 frontend secret 차단
ALLOW_SANDBOX_KEYS_ONLY=false          # SHADOW 에서 LIVE 키 경고 완화
STARTUP_GUARD_STRICT=false             # critical violation 시 부팅 차단 (배포 스크립트 전용)
```

### 6.2 LIVE_CONFIRMATION 의 정확한 값

기본 expected phrase: **`I_UNDERSTAND_LIVE_TRADING_RISK`**.

운영자가 LIVE 활성화 시 `.env.live` 에 다음과 같이 채운다 (실제 파일은 `.gitignore`):

```bash
APP_PROFILE=LIVE
KEY_PROFILE=LIVE
TRADING_MODE=LIVE_MANUAL_APPROVAL
ENABLE_LIVE_TRADING=true
LIVE_CONFIRMATION=I_UNDERSTAND_LIVE_TRADING_RISK
UPBIT_ACCESS_KEY=...  # 출금 권한 OFF + IP allowlist 확인 후
UPBIT_SECRET_KEY=...
```

다른 값(`I_AGREE`, `OK` 등)이면 startup guard 가 즉시 차단.

### 6.3 절대 금지

- 실제 키 / secret / token 문자열 — `.env.example` 에 절대 작성 금지.
- `APP_PROFILE=LIVE` 또는 `KEY_PROFILE=LIVE` 기본값 — 기본은 PAPER.
- `LIVE_CONFIRMATION=...` 에 실제 phrase 채우기 — 빈 값 유지.
- `ENABLE_LIVE_TRADING=true` 기본값.

## 7. `.gitignore` 정책

다음 패턴이 추가되어 실제 키 파일이 절대 commit 되지 않도록 한다 (회귀로 강제):

```
.env
.env.local
.env.*.local
.env.live
.env.shadow
.env.paper
.env.paper.local
.env.test.local
backend/.env
backend/.env.live
backend/.env.shadow
backend/.env.paper
frontend/.env
frontend/.env.local
frontend/.env.live
frontend/.env.shadow
```

`.env.live` / `.env.shadow` / `.env.paper` 실제 파일은 절대 git 추적하지 않는다.

## 8. REST API

### `GET /api/profile`

```jsonc
{
  "app_profile": "PAPER",
  "key_profile": "PAPER",
  "enable_live_trading": false,
  "enable_ai_execution": false,
  "enable_crypto_futures_live": false,
  "require_local_secrets": true,
  "allow_sandbox_keys_only": false,
  "live_confirmation_present": false,
  "violations": [],
  "masked_env_summary": {
    "UPBIT_ACCESS_KEY": "<unset>",
    "OKX_API_SECRET": "<unset>",
    // ... 모든 secret 변수 마스킹
  },
  "has_critical": false,
  "allowed_to_boot": true,
  "updated_at": "2026-05-18T...",
  "warning": "Profile snapshot is read-only. Secrets are masked. ..."
}
```

응답에 secret 평문은 부재. 운영자가 violations 를 확인 후 조치.

## 9. Strict Mode (배포 스크립트 전용)

`enforce_startup_profile(strict=True)` 또는 `STARTUP_GUARD_STRICT=true` env 시 critical
violation 발생하면 `StartupGuardError` 를 raise — 부팅 자체를 중단.

```python
# 배포 스크립트 예
from app.core.profile import enforce_startup_profile, StartupGuardError

try:
    enforce_startup_profile(strict=True)
except StartupGuardError as e:
    print(f"DEPLOY ABORTED: {e}", file=sys.stderr)
    sys.exit(1)
```

기본 앱 startup 은 strict=False — `/api/profile` 응답에 violation 노출 + 운영자가
직접 확인.

## 10. Profile mismatch 예시

| env 조합 | 결과 |
|---|---|
| `APP_PROFILE=PAPER` (기본) | ✅ 통과 |
| `APP_PROFILE=PAPER` + `UPBIT_ACCESS_KEY=<real>` | ❌ `paper_profile_has_live_keys` |
| `APP_PROFILE=PAPER` + `ENABLE_LIVE_TRADING=true` | ❌ `paper_profile_enables_live_trading` |
| `APP_PROFILE=PAPER` + `UPBIT_ACCESS_KEY=__SET_IN_LOCAL_ENV_ONLY__` | ✅ 통과 (placeholder 무시) |
| `APP_PROFILE=PAPER` + `TRADING_MODE=LIVE_MANUAL_APPROVAL` | ❌ `paper_profile_live_trading_mode` |
| `APP_PROFILE=SHADOW` + `OKX_API_SECRET=<real>` | ❌ `shadow_profile_has_trade_keys` |
| `APP_PROFILE=LIVE` + `LIVE_CONFIRMATION=` | ❌ `live_profile_requires_confirmation` |
| `APP_PROFILE=LIVE` + `KEY_PROFILE=PAPER` | ❌ `live_profile_key_profile_mismatch` |
| `APP_PROFILE=LIVE` + only sandbox keys | ❌ `live_profile_only_sandbox_keys` |
| `APP_PROFILE=TEST` + `UPBIT_ACCESS_KEY=<real>` | ❌ `test_profile_has_real_looking_keys` |
| `APP_PROFILE=TEST` + `UPBIT_ACCESS_KEY=fake_test_value` | ✅ 통과 |
| 어떤 profile + `ENABLE_WITHDRAWAL=true` | ❌ `withdrawal_forbidden_in_any_profile` |

## 11. 안전 정책 요약

- 본 단계 완료는 실거래 허가가 아니다 (CLAUDE.md §2.6).
- 출금 권한은 어떤 profile 에서도 금지 (`#27` 정책 + `#28` startup guard).
- LIVE 활성화는 별도 LIVE adapter + ENABLE_LIVE_TRADING + LIVE_CONFIRMATION +
  KEY_PROFILE=LIVE + LIVE 키 채워짐 + sandbox-only flag false 모두 통과해야.
- frontend 에 어떤 거래소 secret 도 노출되지 않는다 (#27 + 본 단계의 `public_env_exposes_secret` 검사).
- `validate_startup_profile` / `enforce_startup_profile` 은 *순수 함수* —
  외부 부작용 없음. 테스트는 `env={...}` 로 완전 격리 가능.
- secret 평문은 본 모듈의 어떤 결과/repr 에도 등장하지 않는다 (`mask_secret`).

## 12. 회귀 테스트

`backend/tests/test_env_profiles.py` — 50+ 케이스. 분류:

1. **Profile enums** (4) — values, parse 안전, properties
2. **Secret classification** (5) — SAFE / PLACEHOLDER / TEST_LOOKING / REAL_LOOKING, low-entropy 안전
3. **PAPER profile** (5) — default / LIVE 키 / LIVE flag / placeholder 허용 / TRADING_MODE=LIVE
4. **SHADOW profile** (2)
5. **LIVE profile** (6) — all gates / no confirmation / no enable / no key profile / sandbox only / sandbox flag
6. **TEST profile** (3)
7. **Withdrawal forbidden** (4 — parametrized across profiles)
8. **Public env** (2)
9. **strict mode** (4)
10. **masked summary** (2)
11. **REST API** (1)
12. **.env.example / .gitignore / docs** (5+)
13. **모듈 정적 회귀** — network import / forbidden literal / Strategy·Agent 직접 import (4)

```
cd backend
python -m pytest tests/test_env_profiles.py -q
```

기존 `tests/test_sandbox_paper_keys.py` (#28 1차) 회귀 없음.

## 13. 후속 단계

- 29번 StrategyBase 이후 — 본 작업 범위 밖.
- LIVE adapter 도입 시 본 startup guard 가 deploy 스크립트에서 strict 모드로 호출.
- KIS adapter (한국투자증권 등 국내 주식 채널) 도입 시 `KeyProfile` 에 `KIS_READONLY`
  등 추가 검토.
