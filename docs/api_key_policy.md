# API Key Policy — 체크리스트 #27 Secret Permissions

본 문서는 Agent Trader Crypto OS 가 외부 거래소·서비스 API 키를 다루는 **유일한
정책 기준**이다. CLAUDE.md §2.1 (절대 안전 원칙) 의 운용 매뉴얼이며, 모든 신규
어댑터·기능 구현 전에 본 문서를 읽고 그대로 따른다.

> 한 줄 요약: **출금 권한 키 영구 금지. Read 전용을 기본, Trade 는 별도 의식적
> 승격, Withdrawal 은 정의하지도 호출하지도 않는다.**

---

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
