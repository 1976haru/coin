# Sandbox / Paper Keys 분리 정책 — 체크리스트 #28

본 문서는 거래소가 제공하는 **Sandbox/Testnet/Demo** 키와 **LIVE** 키, 그리고
시스템 내부의 **Paper** 키 (출금 권한 없는 의도된 가상 키) 를 절대 섞지 않기
위한 운영 규칙이다. CLAUDE.md §2.1 절대 안전 원칙의 운용 매뉴얼이며,
`docs/api_key_policy.md` (#27) 와 함께 읽는다.

> 한 줄 요약: **모드별로 다른 env 변수 이름을 쓴다. 같은 변수에 LIVE 와 sandbox
> 를 번갈아 넣지 않는다. 코드는 mode 별 변수 이름을 보고 분기한다.**

> **2026-05-18 보강**: 본 정책은 **`AppProfile` (PAPER/SHADOW/LIVE/TEST) + StartupGuard**
> 로 코드 레벨에서 강제된다. 자세한 내용은 [`docs/env_profiles.md`](env_profiles.md)
> (#28 2차 확장).

---

## 1. Adapter Mode → Key Tier 매핑

| `AdapterMode` (#20) | 사용 키 | 출처 |
|---|---|---|
| `READ_ONLY` | **없음** | 공개 endpoint 만 |
| `PAPER` | **없음** | 내부 가상 체결 (PaperBroker / MockExchangeAdapter) |
| `SANDBOX` | 거래소 sandbox/testnet 키 | OKX demo / Binance testnet 등 |
| `LIVE` | 거래소 LIVE 키 (출금 권한 OFF, #27) | 정식 운영 |

본 매트릭스는 `app/brokers/base.py::AdapterMode` 와 1:1 대응한다. Adapter 구현은
자기 mode 에 맞지 않는 키가 들어오면 즉시 raise 한다 (#20 강제 패턴).

---

## 2. 환경변수 네이밍 컨벤션

| 변수 prefix | 의미 | 본 시스템 |
|---|---|---|
| `*_API_KEY` | LIVE — 실제 거래용 | LIVE 어댑터에서만 사용 |
| `*_API_KEY_SANDBOX` | 거래소 sandbox/testnet | SANDBOX 어댑터에서만 사용 |
| `*_API_KEY_PAPER` | (선택) 내부 paper 식별자 | 사용하지 않음 — Paper 는 키 자체가 없음 |

### 2.1 거래소별 sandbox 가용성

| 거래소 | Sandbox/Testnet 제공 | 비고 |
|---|---|---|
| **Upbit** | ❌ 없음 | Paper 모드 또는 LIVE 만 |
| **OKX** | ✅ Demo Trading | `OKX_API_KEY_SANDBOX` / `OKX_API_SECRET_SANDBOX` / `OKX_API_PASSWORD_SANDBOX` |
| **Binance** | ✅ Spot Testnet / Futures Testnet | `BINANCE_API_KEY_SANDBOX` / `BINANCE_API_SECRET_SANDBOX` |

Upbit 가 sandbox 를 제공하지 않으므로 Upbit 검증은 PAPER 또는 (출금 OFF) LIVE
**소액**으로만 수행한다.

---

## 3. 절대 금지 패턴

### 3.1 같은 변수 재사용

```bash
# 절대 금지
OKX_API_KEY=demo_key_abc       # 어떤 날 sandbox 로 채움
OKX_API_KEY=live_key_xyz       # 다른 날 LIVE 로 덮어씀
```

→ 운영자가 어느 날 무엇이 들어있는지 추적 불가. 한 줄 차이로 LIVE 트레이딩.

### 3.2 코드에서 mode 무시하고 키 선택

```python
# 절대 금지
def get_okx_key():
    return os.getenv("OKX_API_KEY") or os.getenv("OKX_API_KEY_SANDBOX")
```

→ LIVE 키가 우선 사용되고, sandbox 라고 적힌 환경에서도 실거래 가능.

### 3.3 LIVE 키로 sandbox endpoint 호출

거래소 sandbox 는 별도 endpoint(host)를 가진다. ccxt 는 `set_sandbox_mode(True)` 가
별도 메서드. **mode 와 endpoint 를 분리해서 관리하면 안 된다.** 어댑터의 mode 를
정하면 endpoint 와 키가 한 묶음으로 결정된다.

---

## 4. 권장 구현 패턴

### 4.1 Settings 분리 (예시 — 추후 LIVE/SANDBOX 어댑터 구현 시)

```python
@dataclass(frozen=True)
class Settings:
    # LIVE
    okx_api_key:           str = os.getenv("OKX_API_KEY", "")
    okx_api_secret:        str = os.getenv("OKX_API_SECRET", "")
    okx_api_password:      str = os.getenv("OKX_API_PASSWORD", "")
    # SANDBOX
    okx_api_key_sandbox:    str = os.getenv("OKX_API_KEY_SANDBOX", "")
    okx_api_secret_sandbox: str = os.getenv("OKX_API_SECRET_SANDBOX", "")
    okx_api_password_sandbox: str = os.getenv("OKX_API_PASSWORD_SANDBOX", "")
```

### 4.2 Adapter 생성

```python
# 예시 (LIVE/SANDBOX 어댑터 구현 시 — 본 PR 범위 외)
def make_okx_adapter(mode: AdapterMode, settings: Settings):
    if mode == "LIVE":
        return OkxLiveAdapter(
            api_key=settings.okx_api_key,
            api_secret=settings.okx_api_secret,
            api_password=settings.okx_api_password,
        )
    if mode == "SANDBOX":
        return OkxSandboxAdapter(
            api_key=settings.okx_api_key_sandbox,
            api_secret=settings.okx_api_secret_sandbox,
            api_password=settings.okx_api_password_sandbox,
        )
    if mode == "READ_ONLY":
        return OkxAdapter()       # 키 안 받음 (현재 구현)
    raise ValueError(f"Unsupported mode for Okx: {mode}")
```

### 4.3 Settings.validate() 가 검증

`app/core/config.py::Settings.validate()` 는 다음을 경고한다:

- TRADING_MODE 가 PAPER/SIMULATION 인데 LIVE 키 (`*_API_KEY`) 가 비어있지 않음
  → 운영자에게 **"왜 paper 모드인데 live 키가 .env 에 있는지"** 묻는 알림.

이 경고는 강제 차단이 아닌 알림이다 — dev 환경에서 정상이지만, 운영 환경에서
실수로 LIVE 키만 채워둔 채 PAPER 모드로 돌리는 footgun 을 줄인다.

---

## 5. 운영 점검표

배포 전 다음을 확인:

- [ ] `TRADING_MODE` 값과 채워진 키의 종류가 일치
- [ ] LIVE 모드인데 sandbox 키만 .env 에 있지 않음
- [ ] sandbox 어댑터를 만들 때 LIVE 키 변수를 사용하지 않음
- [ ] Settings.validate() 경고 0건
- [ ] `.env.example` 에 sandbox 변수 슬롯이 명시되어 있음

---

## 6. 현재 시스템 상태 (2026-05-10 기준)

본 문서 작성 시점에서:

- **READ_ONLY 어댑터** 만 구현되어 있음 (Upbit/OKX/Binance) — 키 자체 받지 않음
- **PAPER** 는 `MockExchangeAdapter` / `PaperBroker` — 키 없음
- **SANDBOX 어댑터 — 미구현**. 도입 시 본 정책의 §4 패턴 그대로 따른다.
- **LIVE 어댑터 — 미구현**. Paper 4주 + Shadow 2주 + 300건 검증 통과 후 별도 PR.

---

## 7. 관련 문서/코드

| 항목 | 위치 |
|---|---|
| AdapterMode | `backend/app/brokers/base.py` |
| API Key 정책 (출금 금지) | `docs/api_key_policy.md` (#27) |
| Settings + validate | `backend/app/core/config.py` (#9) |
| Feature Flags | `backend/app/core/feature_flags.py` (#10) |
| READ_ONLY 어댑터 (키 거부) | `backend/app/brokers/{upbit,okx,binance}_adapter.py` |
| PaperBroker / MockExchangeAdapter | `backend/app/brokers/{paper,mock}_broker.py` |
| 회귀 테스트 | `backend/tests/test_sandbox_paper_keys.py` |
