# API Key Policy — 체크리스트 #27 Secret Permissions

본 문서는 Agent Trader Crypto OS 가 외부 거래소·서비스 API 키를 다루는 **유일한
정책 기준**이다. CLAUDE.md §2.1 (절대 안전 원칙) 의 운용 매뉴얼이며, 모든 신규
어댑터·기능 구현 전에 본 문서를 읽고 그대로 따른다.

> 한 줄 요약: **출금 권한 키 영구 금지. Read 전용을 기본, Trade 는 별도 의식적
> 승격, Withdrawal 은 정의하지도 호출하지도 않는다.**

---

## 0.1 관련 문서

- 환경 프로파일 + Startup Guard: [`docs/env_profiles.md`](env_profiles.md) (#28) —
  PAPER/SHADOW/LIVE/TEST 프로파일 분리 + 부팅 시 키 정합성 검증.
- Sandbox/Paper Keys 분리 정책: [`docs/sandbox_paper_keys.md`](sandbox_paper_keys.md) (#28 1차).

## 1. 권한 등급 (Permission Tier)

거래소 API 키는 권한 조합으로 정의되지만, 본 시스템은 다음 **3단계 등급**으로
관리한다:

| Tier | 의미 | 본 시스템 사용 |
|---|---|---|
| **READ** | 시세/호가/계좌 조회만 | 기본. 모든 어댑터의 시작 모드. |
| **TRADE** | READ + 매매 주문 송신 | 별도 LIVE 어댑터 + ENABLE_LIVE_TRADING 통과 후에만. |
| **WITHDRAWAL** | TRADE + 출금/이체 | **영구 금지.** 키 발급 자체를 하지 않는다. |

### 1.1 거래소별 권한 매핑

| 거래소 | READ 권한 | TRADE 권한 | 절대 발급 금지 |
|---|---|---|---|
| **Upbit** | "조회" | "주문" | "출금", "내부 이체", "API 출금" |
| **OKX** | "Read" | "Trade" | "Withdraw", "Transfer", "Sub-account Transfer" |
| **Binance** | "Enable Reading" | "Enable Spot & Margin Trading", "Enable Futures" (#67 후) | "Enable Withdrawals", "Enable Internal Transfer", "Enable Universal Transfer" |

### 1.2 코드 강제

| 보호 | 메커니즘 |
|---|---|
| `ExchangeAdapter` 에 출금 메서드 정의 자체 금지 | `assert_no_withdrawal_methods(cls)` (CI 회귀) |
| READ_ONLY 어댑터에 API 키 주입 시 즉시 raise | `UpbitAdapter`/`OkxAdapter`/`BinanceAdapter` 생성자 |
| `ENABLE_WITHDRAWAL` 환경변수 영구 false | `app/core/feature_flags.py` 에서 하드코드 |
| `.env` / 코드에 시크릿 잔존 차단 | `.gitignore` + `security-ci.yml` grep + `app/audit/redaction.py` |

---

## 2. 키 발급 절차

새 LIVE 키를 발급할 때:

1. **거래소 콘솔에서 새 API 키 생성**
2. **출금 관련 권한 모두 OFF 확인** (위 §1.1 표 참조). UI에 체크박스가 있으면
   default 가 OFF 이더라도 다시 한번 확인.
3. **IP whitelist 필수** — 배포 환경의 고정 IP만 허용. 동적 IP/공유 IP 금지.
4. **API key 별도 — 절대 SUB-account 의 master 키 사용 금지.**
5. **2FA 필수** — 거래소 계정 자체 2FA 활성.
6. **저장**: `.env` 파일에만. 클라우드 비밀 관리(예: 1Password CLI, GitHub
   Secrets) 사용 시 권한이 보호되는지 별도 검증.
7. **검증**:
   ```bash
   # 권한 점검 — 어댑터 LIVE 모드 활성 전 필수
   python scripts/verify_api_key_permissions.py --exchange upbit
   ```
   (스크립트 미구현 시 거래소 UI 에서 직접 권한 목록 확인)

---

## 3. 저장·전달 규칙

### 3.1 저장 위치

| 위치 | 허용? | 비고 |
|---|---|---|
| `.env` 로컬 파일 | ✅ | `.gitignore` 처리 필수 |
| 환경변수 (OS) | ✅ | systemd / Docker `--env-file` |
| GitHub Secrets (CI) | ⚠️ | CI 가 LIVE 키 사용 안 함. paper/sandbox 만. |
| `.env.example` | ❌ | 변수명만 적고 값은 비움 |
| Code/주석/커밋 | ❌ | 어떤 형태로든 |
| 로그/audit 출력 | ❌ | `app/audit/redaction.py` 가 자동 마스킹 |
| Frontend bundle | ❌ | `frontend/src` 에 secret 패턴 부재를 회귀 테스트 검증 |
| Slack/이메일/PR 본문 | ❌ | 절대 |

### 3.2 전달 시

- **직접 입력만** — 채팅/PR/이메일 본문으로 전달 금지.
- **노출 시 즉시 폐기** — 거래소 콘솔에서 키 삭제 후 새로 발급.

---

## 4. 운영 점검표

배포 전 다음을 모두 확인 (`#91 Pre-market Checklist` 와 통합):

- [ ] **출금 권한 OFF 검증** — 거래소 콘솔에서 직접 확인
- [ ] **IP whitelist** — 배포 IP 만 허용
- [ ] **`.env` 가 git 추적 안 됨** — `git ls-files | grep '.env$'` 결과 빈 줄
- [ ] **`.env.example` 에 실제 값 없음** — 자동 회귀 (`test_config_layer`)
- [ ] **frontend bundle 에 secret 패턴 없음** — `test_frontend_skeleton.py::test_frontend_source_has_no_committed_secrets`
- [ ] **`ENABLE_WITHDRAWAL=false`** — `feature_flags.py` 에 영구 하드코드, 환경변수로 변경 불가
- [ ] **ADMIN_TOKEN 변경됨** — `Settings.validate()` 가 기본값 사용 시 경고
- [ ] **/api/config/effective 응답에 secret 마스킹됨** — `test_config_layer.py::test_api_config_effective_returns_redacted_summary`

---

## 5. 사고 대응 (Incident Response)

키 노출이 의심될 때:

1. **즉시 거래소 콘솔에서 해당 키 비활성화/삭제.**
2. **킬스위치 활성화** — `POST /api/kill-switch` (admin 토큰).
3. **AuditLog 검토** — `redact()` 가 마스킹했는지, secret 문자열이 평문으로 남아있는지.
4. **포지션 점검** — 무단 거래 흔적 확인. 의심 시 거래소에 제시.
5. **새 키 발급** — §2 절차 그대로.
6. **사후 보고서** — `docs/incidents/YYYY-MM-DD-<slug>.md` 작성. 원인·영향·재발 방지.

---

## 6. 관련 코드/문서

| 항목 | 위치 |
|---|---|
| 어댑터 인터페이스 | `backend/app/brokers/base.py` (출금 메서드 정의 금지) |
| READ_ONLY 어댑터 (API 키 거부) | `backend/app/brokers/{upbit,okx,binance}_adapter.py` |
| Feature Flags | `backend/app/core/feature_flags.py` (`ENABLE_WITHDRAWAL` 영구 false) |
| Redaction | `backend/app/audit/redaction.py` |
| Audit Log | `backend/app/audit/audit_log.py` |
| Config validation | `backend/app/core/config.py` (`Settings.validate`) |
| Sandbox/Paper Keys 분리 정책 | (#28 항목에서 작성 예정) |
| Secret CI scan | `.github/workflows/security-ci.yml` |

---

## 7. 미해결 항목

- `scripts/verify_api_key_permissions.py` — 거래소별 권한 조회 스크립트 (어댑터
  LIVE 모드 시작 시 추가 예정)
- `docs/incidents/` — 사고 보고서 템플릿 (실제 사고 발생 시 작성)

---

## 8. 거래소별 권한 상세 정책

### 8.1 Upbit 권한 정책

**기본 운영**: 시세는 public quotation endpoint 로 read-only (`UpbitPublicClient`, #21).
**잔고 조회** 가 필요하면 별도 *조회* 권한만 검토. **주문 권한** 은 paper/shadow
검증 + 별도 LIVE adapter + 별도 승인 단계 통과 전 금지. **출금 권한** 은 절대 발급
금지.

| 권한 | 본 시스템 |
|---|---|
| 조회 (계좌/주문 조회) | 별도 승인 후 제한적 허용 |
| 주문 (place/cancel) | live 승인 전 금지 |
| 출금 | **절대 금지 (영구)** |
| 입출금 주소 관리 | **금지** |
| API 발급/회수 | **금지** |

운영 노트:
- 주문 또는 출금 권한 선택 시 거래소가 IP whitelist 등록을 요구한다 — 운영자가
  반드시 등록.
- Secret Key 는 발급 직후 한 번만 표시되고 재확인 불가 — 별도 secret manager 에
  안전 보관하되 repository / git 에는 절대 저장 금지.
- 출금 API 와 출금 가능 정보 조회 API 는 운영 키에서 사용하지 않는다.

### 8.2 OKX 권한 정책

**기본 운영**: market data 는 public endpoint 로 read-only (`OkxPublicClient`,
#22). **account/trade 권한** 은 기본 비활성. **futures/swap LIVE** 권한은 별도
phase + 별도 승인 전 금지.

| 권한 | 본 시스템 |
|---|---|
| Read | 별도 승인 후 제한적 허용 |
| Trade | live 승인 전 금지 |
| Withdraw | **절대 금지 (영구)** |
| Transfer | **금지** |
| Sub-account Transfer | **금지** |
| Futures/Swap | 별도 phase 전 금지 |

운영 노트:
- API key + secret + passphrase **세 값 모두** 필요 — 셋 다 동등하게 비밀로 보호.
- IP allowlist 필수.
- OKX `code=50011` 응답 = rate-limit 신호 (`RateLimitGuard` 가 자동 cooldown, #26).
- 본 시스템의 `OkxTradeClient` 는 모든 trade endpoint 가 disabled stub — 실제
  주문은 별도 LIVE adapter 에서만.

### 8.3 Binance 권한 정책

**기본 운영**: Spot public market data 만 read-only (`BinancePublicClient`, #23).
모든 trading/account/futures 권한은 **규제·지역 제한 확인 전 금지**. 본 시스템의
`BinanceAccountClient` / `BinanceTradeClient` 는 *credentials 가 들어와도 모든
메서드가 disabled* — regulatory gate.

| 권한 (Binance UI 명칭) | 본 시스템 |
|---|---|
| Enable Reading | 별도 승인 후 제한적 허용 |
| Enable Spot & Margin Trading | live 승인 전 금지 |
| Enable Futures | 별도 phase (#67) 전 금지 |
| Enable Withdrawals | **절대 금지 (영구)** |
| Enable Internal Transfer | **금지** |
| Enable Universal Transfer | **금지** |
| Enable Vanilla Options | **금지** |
| Permits Universal Transfer (sub-account) | **금지** |

운영 노트:
- **Binance Global ≠ Binance.US** — 운영자가 명시적으로 어느 거래소인지 선언.
  키 발급도 별도. IP/규제 영역에 맞춰 한쪽만 사용.
- IP restriction 필수 — Binance UI 에서 "Restrict access to trusted IPs only".
- 한국 사용자 이용 가능성, 약관, KYC 는 변동 가능 — live 활성화 전 재확인.

---

## 9. 권한 체크리스트 (통합 표)

본 시스템의 모든 거래소·권한 조합 요약. 각 권한은 `MUST_OFF` / `DEFAULT_OFF` /
`READ_LIMITED` / `TRADE_GATED` 중 하나로 분류.

| 항목 | 허용 여부 | 비고 |
|---|---|---|
| Read market data (public quotation) | **OK** | 본 시스템 기본. API key 없이도 호출 가능 |
| Read account balance | `READ_LIMITED` | 별도 승인 + IP allowlist 필수 |
| Read order history | `READ_LIMITED` | 별도 승인 |
| Place spot order | `TRADE_GATED` | live 승인 후 제한적. PAPER 충분 검증 필수 |
| Cancel spot order | `TRADE_GATED` | live 승인 후 제한적 |
| Place futures/swap order | `DEFAULT_OFF` | 별도 phase (#67), 별도 승인 |
| Margin order | `DEFAULT_OFF` | 별도 phase |
| Set leverage | `DEFAULT_OFF` | 별도 phase |
| Withdrawal | **`MUST_OFF` (영구)** | 어떤 단계에서도 발급 금지 |
| Deposit address management | **`MUST_OFF`** | 발급 금지 |
| Internal transfer | **`MUST_OFF`** | 발급 금지 |
| Sub-account transfer | **`MUST_OFF`** | 발급 금지 |
| Universal transfer | **`MUST_OFF`** | 발급 금지 |
| API key management (via API) | **`MUST_OFF`** | 발급 금지 |
| IP allowlist | **필수 또는 강력 권장** | 모든 LIVE 키 |

코드 강제:
- `MUST_OFF` 권한에 해당하는 메서드는 base / mock / paper / 실거래 adapter 어디에도
  *정의되지 않는다* — `assert_no_withdrawal_methods` 회귀가 강제.
- `DEFAULT_OFF` 권한은 adapter capability 가 `False` 로 시작 — capability 변경은
  별도 phase 의 코드 변경을 요구.
- `TRADE_GATED` 권한은 OrderGateway 단일 경로 + `ENABLE_LIVE_TRADING=true` + 별도
  승인 절차 통과 후에만 동작.

---

## 10. 스크린샷 보관 가이드

거래소 콘솔에서 API key 발급/권한 설정 화면 스크린샷은 **증빙 목적** 으로만
보관한다. 다음 규칙을 따른다.

### 10.1 캡처 범위

- 권한 체크박스/스위치가 보이는 화면 전체.
- 키 발급 직후 "출금/이체 권한 OFF" 상태가 명확히 보이는 화면.
- IP allowlist 등록 화면.

### 10.2 마스킹 의무 — 캡처 *전* 또는 *후*

다음 정보가 절대 화면에 남아있지 않도록 마스킹:

| 마스킹 대상 | 비고 |
|---|---|
| API key 문자열 | 발급 직후만 한 번 표시되는 secret. 즉시 ✗ 처리 |
| Secret key 문자열 | 동상 — 절대 노출 금지 |
| Passphrase | OKX 등 |
| QR 코드 | 키/secret 의 시각화 — 절대 노출 금지 |
| Account 일련번호 / 이메일 | 부분 마스킹 (`use***@example.com`) |
| 2FA 코드 / 백업 코드 | 절대 노출 금지 |
| 출금 화이트리스트 주소 | 식별 위험 — 부분 마스킹 |

### 10.3 저장 위치

- **repository 밖** — `~/secrets/exchange-permissions/` 등 로컬 보관.
- **cloud storage** 사용 시 접근 권한 제한 (운영자 본인만).
- **PR / issue / chat / wiki 에 원본 업로드 금지.**

### 10.4 파일명 규칙

`api-permission-<exchange>-<tier>-<YYYYMMDD>-redacted.png`

예시:
```
api-permission-upbit-readonly-20260518-redacted.png
api-permission-okx-readonly-20260518-redacted.png
api-permission-binance-readonly-20260518-redacted.png
```

`-redacted` 접미사가 없는 파일은 절대 공유하지 않는다.

### 10.5 보존 기간 / 폐기

- 키 회전 또는 폐기 시 해당 스크린샷도 함께 폐기 (운영 기록은 메타정보만 유지).
- 사고 대응 시점에서 forensic 자료가 필요한 경우 별도 안전 위치로 이동.

---

## 11. `.env.example` 작성 원칙

`.env.example` 은 **변수명 카탈로그** 이지 secret 저장소가 아니다.

### 11.1 허용

- 변수명 + 빈 값 (`UPBIT_ACCESS_KEY=`).
- 변수명 + 명시적 placeholder (`UPBIT_ACCESS_KEY=__SET_IN_LOCAL_ENV_ONLY__`).
- 변수 그룹별 주석.

### 11.2 금지

- 실제 키 / secret / token 문자열.
- 실제 키처럼 보이는 더미 값 (긴 영숫자 시퀀스).
- `ENABLE_LIVE_TRADING=true` / `ENABLE_AI_EXECUTION=true` 같은 위험 flag 의 true.
- `ENABLE_WITHDRAWAL` 변수 자체 — 환경변수로 변경 불가하다는 정책 (코드에서 영구
  false).

### 11.3 frontend `.env`

frontend 의 `.env` / `.env.example` 에는 **secret 을 절대 두지 않는다.**

- `VITE_*` / `NEXT_PUBLIC_*` 접두사 변수에 secret 금지 — 이 변수들은 빌드 시
  bundle 에 포함되어 브라우저로 노출된다.
- 거래소 API key/secret/passphrase 는 backend 전용 — frontend 는 백엔드 API 로만
  통신.

본 시스템의 `.env.example` 은 모든 secret 변수를 *빈 값* 으로 둔다. 본 정책의
backward-compatible 한 표현.

---

## 12. 운영 전 확인 절차 (배포 전 체크리스트)

LIVE 활성화 또는 새 키 도입 전 운영자가 반드시 확인:

1. **권한 스크린샷 확인** — `withdrawal disabled` / `transfer disabled` / 모든
   `MUST_OFF` 권한이 OFF.
2. **IP allowlist 등록 확인** — 배포 환경의 고정 IP만.
3. **최소 권한 확인** — READ 만, 또는 READ + 제한적 TRADE.
4. **paper / shadow 검증 결과 확인** — 동일 전략이 최소 N일 안정적으로 동작.
5. **`ENABLE_LIVE_TRADING=false`** — 키 도입 직후에도 flag 는 false 유지.
6. **emergency stop / kill switch** 동작 확인.
7. **small notional dry-run 계획** — 첫 LIVE 거래는 최소 notional 로 시작.
8. **사고 대응 절차** (§5 / §13) 운영자가 숙지.
9. **AuditLog redaction** 동작 확인 (`test_config_layer.py`).

---

## 13. 위반 시 대응

키 노출 / 미허가 권한 발견 / 출금 권한 발견 등의 사고 시:

1. **즉시 거래소 콘솔에서 해당 키 비활성화/삭제.**
2. **킬스위치 활성화** — `POST /api/kill-switch` (admin token).
3. **secret manager / env 회전** — 모든 위치의 키 즉시 변경.
4. **git history secret scan** — `gitleaks` / `truffleHog` 등으로 history 검색.
   secret 발견 시 history rewrite + force-push + 거래소 키 회전 동시 진행.
5. **AuditLog / 운영 로그 검토** — `redact()` 가 적용되었는지, secret 문자열이
   평문으로 남아있는지. 평문 발견 시 즉시 삭제 또는 마스킹.
6. **거래소 출금/주문 내역 확인** — 무단 거래 / 출금 흔적. 의심 시 거래소에 제시.
7. **포지션 점검** — 의도하지 않은 포지션 즉시 청산.
8. **사후 보고서** — `docs/incidents/YYYY-MM-DD-<slug>.md` 작성. 원인 / 영향 /
   재발 방지.

---

## 14. 이번 단계(#27)의 범위

본 단계는 **정책 문서 + 회귀 테스트** 작성만이다.

- 실제 API key / secret / passphrase 입력 없음.
- 실제 LIVE 주문 활성화 없음.
- 28번 Sandbox/Paper Keys 분리 정책은 별도 단계 (`docs/sandbox_paper_keys.md`).
- 본 단계 완료는 **실거래 허가가 아니다.** LIVE 활성화는 별도 LIVE adapter +
  ENABLE_LIVE_TRADING + 별도 승인 절차 통과 후에만.

### 14.1 코드 보조 도구

| 도구 | 위치 | 역할 |
|---|---|---|
| `mask_secret(value)` | `backend/app/audit/secret_masking.py` | repr/log 출력 시 secret 값 마스킹 |
| `redact(payload)` | `backend/app/audit/redaction.py` | dict/list 의 키-기반 자동 마스킹 |
| `check_secret_policy.py` | `scripts/check_secret_policy.py` | repository 정적 스캔 (수동 실행) |
| `assert_no_withdrawal_methods(cls)` | `backend/app/brokers/base.py` | 어댑터 출금 메서드 부재 회귀 |
| Settings.summary() redaction | `backend/app/core/config.py` | `/api/config/effective` 응답 마스킹 |
