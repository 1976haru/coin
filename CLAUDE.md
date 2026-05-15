# CLAUDE.md — Agent Trader Crypto OS v1 작업 원칙

이 문서는 Claude Code가 이 저장소에서 작업할 때 **반드시 지켜야 하는 원칙과 금지사항**을 정의한다.
체크리스트 항목 진행 중 의문이 들면 이 문서가 최종 기준이다.

---

## 1. 정체성

- 프로그램명: **Agent Trader Crypto OS v1**
- 목적: AI Agent 기반 코인 자동매매 **연구·검증·관제 플랫폼** (단순 자동매매 봇 아님)
- 사용자: 비개발자. 결과물은 PC 브라우저와 스마트폰에서 쉽게 확인 가능해야 한다.
- 저장소: `https://github.com/1976haru/coin`

---

## 2. 절대 안전 원칙 (Hard Rules)

위반 시 작업 즉시 중단하고 사용자에게 보고. 이 규칙은 어떤 효율/요청보다 우선한다.

### 2.1 실거래 금지 기본
1. 실제 거래소 LIVE order 호출 금지.
2. 출금 권한이 부여된 API Key 사용 금지. `ENABLE_WITHDRAWAL`은 존재하더라도 무조건 false이며 구현하지 않는다.
3. API Key, Secret, Passphrase, Telegram Token, 계좌번호, PII를 코드/커밋/로그에 남기지 않는다.
4. `.env`는 커밋 금지. `.env.example`은 변수명만 적고 값은 비운다.
5. frontend에는 어떤 secret도 저장하지 않는다.
6. GitHub Pages는 mock/demo UI 전용. 실제 backend는 local/private 환경에서만 실행한다.

### 2.2 기본값 false 플래그
| 플래그 | 기본 | 의미 |
|---|---|---|
| `ENABLE_LIVE_TRADING` | false | 실제 주문 전송 |
| `ENABLE_AI_EXECUTION` | false | AI 자동 실행 |
| `ENABLE_CRYPTO_FUTURES_LIVE` | false | 선물 실거래 |
| `ENABLE_KIMP_STRATEGY` | false (또는 paper-only) | 김프/역김프 전략 |
| `ENABLE_LIVE_ORDER_SUBMISSION` | false | 주문 송신 |
| `ENABLE_WITHDRAWAL` | false (영구) | 출금 |

새 위험 기능은 항상 추가 플래그를 도입하고 기본 false로 시작한다.

### 2.3 AI Agent 안전
- AI Agent는 **분석·추천·설명만** 한다. 직접 주문 금지.
- AgentDecision 객체는 `is_order_intent=false`를 기본값으로 가진다.
- AI Agent / Strategy / Frontend는 `BrokerAdapter`, `ExchangeAdapter`, `OrderExecutor`를 직접 import 금지.
- RiskOfficerAgent가 최종 거부권. REJECT 시 어떠한 주문 후보도 생성하지 않는다.
- 낮은 confidence 판단은 `WATCH_ONLY` 처리.

### 2.4 단일 주문 경로 (우회 금지)

```text
StrategySignal
  → AgentReview
  → RiskManager
  → OrderGuard
  → PermissionGate
  → ApprovalQueue
  → OrderGateway
  → PaperExecutor / ShadowExecutor / Future Live Executor
  → BrokerAdapter
  → AuditLog
```

이 경로를 우회하는 새 코드는 작성 금지. PR/커밋에서 발견 시 즉시 거절.

### 2.5 자동 진입 차단 조건
다음 중 하나라도 참이면 **신규 BUY/진입 자동 차단**:
- WebSocket reconnecting 중
- stale data (N초 이상 미수신)
- quote missing
- 환율 이상치
- 거래소 공지 위험 (입출금 중단, 유의종목, 상폐)

청산(SELL)은 위험 축소 목적의 별도 정책으로 관리하고 BUY보다 관대하게 허용한다.

### 2.6 승격 정책
체크리스트 PASS는 **실거래 허가가 아니다**. LIVE 활성화는 별도 수동 승인, 별도 환경변수, 별도 문서, 별도 테스트 모두 통과한 후에만 가능.

---

## 3. 코드 작성 원칙

### 3.1 모듈 경계
- `app.agents.*`는 `app.brokers.*`, `app.execution.paper_executor`, `app.execution.shadow_executor`를 직접 import 금지.
- `app.strategies.*`는 `app.brokers.*`, `app.execution.*`를 직접 import 금지. Signal 객체만 반환.
- `frontend/`는 거래소 API/AI API를 직접 호출 금지. 반드시 backend/ 경유.

### 3.2 새 파일/모듈 추가 시
- 체크리스트 번호를 docstring에 표기 (예: `# 체크리스트 #16 Data Freshness`)
- `is_order_intent`, `confidence`, `reason`은 신호/판단 객체의 필수 필드
- 모든 차단/거절은 `AuditLog.record()`로 이벤트 기록

### 3.3 테스트
- 신규 위험 기능은 차단 회귀 테스트를 함께 작성 (예: `test_default_flag_false`)
- 모든 신규 strategy/agent는 단위 테스트 동반
- pytest 통과 없이 커밋 금지

### 3.4 문서
- 새 기능은 `docs/checklist_progress.md`에 PASS 표기
- 산출물 파일이 있으면 체크리스트 매핑 갱신

---

## 4. 작업 진행 방식

### 4.1 체크리스트 우선
- 96개 체크리스트 항목을 번호 순으로 진행. 임의 점프 금지.
- 한 항목 = 코드 + 테스트 + 문서 + 커밋
- PASS 기준은 항목별 "완료 기준" 컬럼

### 4.2 격리(Quarantine) 폴더
- `backend/_legacy_innogrit/` 의 코드는 **참고 전용**.
- 활성 코드에서 import 금지.
- 아이디어를 가져올 때는 새 모듈에 깔끔하게 재구현. 직접 복사 금지.

### 4.3 위험 작업 사전 확인
다음은 사용자 확인 후 실행:
- 거래소 API를 호출하는 코드 추가 (mock/paper 외)
- LIVE_* 플래그 기본값 변경
- `_legacy_innogrit/`에서 활성 트리로 코드 복원
- 의존성 추가 중 거래소 SDK (ccxt, pyupbit 등)
- CI workflow 변경 중 secret 사용 부분

### 4.4 메모리/문서
- 사용자가 정한 규칙·결정은 `~/.claude/projects/.../memory/` 의 적절한 메모리에 저장
- 코드만 봐도 알 수 있는 내용은 메모리에 넣지 않는다

---

## 5. 금지된 것 한 줄 요약

> 실거래 호출 금지 · 출금 권한 키 금지 · secret 커밋 금지 · AI 직접 주문 금지 · 단일 경로 우회 금지 · 기본 플래그 변경 금지 · 격리 폴더 활성화 금지

---

## 6. 참조

- 체크리스트: `C:\아이디어\coin\agent_trader_crypto_os_v6_structure_checklist.xlsx` (96항목)
- 안전 원칙 상세: `docs/safety_principles.md`
- 운용 모드 상세: `docs/operating_modes.md`
- 아키텍처: `docs/architecture.md`
- 진척도: `docs/checklist_progress.md`
- 재구조화 매핑: `docs/restructure_mapping.md`
