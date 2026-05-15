# Config Layer — 체크리스트 #9

## 1. 설계 결정: 환경변수 단일 진리 소스

본 프로젝트는 **YAML/TOML 설정 파일을 도입하지 않는다.** 모든 런타임 설정은
`backend/app/core/config.py` 의 `Settings` (frozen dataclass)가 환경변수에서만 읽는다.

### 근거

1. **Secret 누출 위험 최소화** — `.env`/환경변수는 `.gitignore` + CI secret-scan으로
   커밋 차단이 명확하다. YAML 은 실수로 secret 이 함께 들어갈 가능성이 높다.
2. **12-factor / 컨테이너 친화** — Docker, Tailscale, GitHub Actions 모두
   환경변수 주입이 표준 패턴.
3. **운영 단순성** — 한 위치(`.env`)만 관리하면 된다.
4. **frozen dataclass + lru_cache** 로 런타임 변경 차단. 변경 시 프로세스 재시작.

## 2. 단일 위치

| 파일 | 역할 |
|---|---|
| `backend/app/core/config.py` | `Settings` 정의 + `get_settings()` |
| `backend/app/core/feature_flags.py` | 위험 ENABLE_* 플래그를 별도로 모은 view |
| `backend/app/core/modes.py` | TradingMode enum + ModeCapability matrix |
| `.env.example` | 사용 가능한 모든 env 변수의 카탈로그 (값은 비움) |

`config.py::ENV_VARS_REFERENCED` 와 `.env.example` 의 키 집합이 **회귀 테스트로 동기화 강제** (`test_config_layer.py`).

## 3. Introspection API

### `Settings.summary()` — 마스킹된 스냅샷
```python
{
  "trading_mode": "PAPER",
  "enable_live_trading": false,
  ...
  "okx_api_key": "***REDACTED***",
  "anthropic_api_key": "***REDACTED***",
  "admin_token": "***REDACTED***",
}
```
`app.audit.redaction.redact` 와 동일한 규칙 사용. admin UI 에서 안전 노출 가능.

### `Settings.validate()` — 운영 경고
- ADMIN_TOKEN 기본값 사용 시 경고
- 모드/플래그 불일치 (LIVE 모드 + ENABLE_LIVE_TRADING=false 등)
- 비-PAPER 모드의 비보수적 한도 (notional/일 손실/레버리지)

빈 리스트면 OK. 위반 시 프로세스 중단하지 않고 운영자에게 노출만 한다.

## 4. REST 엔드포인트

| 엔드포인트 | 인증 | 용도 |
|---|---|---|
| `GET /api/status` | 공개 | 모드/플래그/경고 요약 (`safety_warnings` 필드) |
| `GET /api/config/warnings` | 공개 | `Settings.validate()` 결과만 |
| `GET /api/config/effective` | admin | `Settings.summary()` 전체 (마스킹됨) |

## 5. 변경 절차

새 환경변수를 추가할 때:

1. `Settings` 에 필드 추가 (`os.getenv` 또는 `_bool/_int/_float`)
2. `ENV_VARS_REFERENCED` 튜플에 키 추가
3. `.env.example` 에 같은 키 추가 (값은 비움)
4. 회귀 테스트(`test_config_layer.py`)가 자동으로 파리티 검증
5. 위험 기능이면 `validate()` 에 mode/flag 정합성 점검 추가

## 6. 미도입 항목 (의도적)

- **YAML/TOML 설정 파일** — 위 1번 근거로 도입하지 않음. 향후 dev-experience 요구가
  강해지면 `python-dotenv` 의 `.env.local` 우선순위 + `.env` fallback 으로
  확장 가능 (현재 `python-dotenv` 가 이미 dependencies 에 있음).
- **환경별 프로파일 (dev/staging/prod)** — TRADING_MODE 와 ENABLE_* 조합으로 충분.
  별도 프로파일 파일 도입 시 secret 분기 위험 증가.
