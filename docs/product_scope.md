# Product Scope — Agent Trader Crypto OS v1

> 체크리스트 **#1** — 산출물의 단일 진리 소스.
> 원칙: 처음부터 실전 자동수익 시스템으로 가지 않는다. 통제와 검증이 무너진다.

---

## 1. 정체성

**Agent Trader Crypto OS v1**

> AI Agent 기반 코인 자동매매 **연구·검증·관제 플랫폼**.

이 프로그램은 다음이 **아니다**:
- 단순 코인 자동매매 봇
- 즉시 수익을 내는 트레이딩 머신
- 사용자 대신 거래소 버튼을 눌러주는 자동화 도구

이 프로그램은 다음**이다**:
- AI Agent가 **시장 상태 / 전략 후보 / 리스크 / 실행 후보**를 분석하는 OS
- 사용자가 **관제(operate)** 하는 시스템 (PC + 스마트폰)
- 모든 주문성 결정을 **단일 경로 + 감사 로그**로 통제하는 검증 인프라

---

## 2. 사용자

- **비개발자**. 결과물은 PC 브라우저와 스마트폰에서 한눈에 이해되어야 한다.
- 작년 이노그릿(역김프 봇) 실패 경험에서 **실전 안정성**의 가치를 학습한 사용자.
- 리스크에 보수적, 관측 가능성(observability)을 가장 중시.

---

## 3. 제품 구조 (MOCA식 허브+모듈)

이노그릿/MOCA의 **허브 + 카테고리 카드** 패턴을 차용한다.

### 3.1 허브 (Hub) — Dashboard
한 페이지에서 다음을 카드로 본다:
- **System Status** — 모드, 플래그, KillSwitch, 일 손익, 연속 손실, 승인 대기
- **Agent Council** — 각 Agent의 마지막 판단 + confidence + 근거 한 줄
- **Strategy Portfolio** — 4개 전략 카드 (활성/비활성, 신호 수, 마지막 시그널)
- **Risk** — 현재 한도 사용률, 위반 알림
- **Approvals** — 대기 중인 사람 승인 카드
- **Logs** — 최근 감사 이벤트 스트림

### 3.2 모듈 (Module) — 영역별 페이지
각 카드를 클릭하면 전용 페이지로:
- `/agent` — Agent별 상세 (입력/출력/이력/성과)
- `/strategy` — 전략별 백테스트/Paper 결과
- `/kimp` — 김프/역김프 특수 모니터링
- `/risk` — RiskManager 한도/사용률/이력
- `/approval` — 승인 큐 (제안 카드 + 근거 + TTL)
- `/logs` — 감사 로그 검색/필터
- `/settings` — 모드/플래그 (변경은 admin 토큰)
- `/help` — 사용법, 안전 원칙, 버전 정보

### 3.3 카드의 공통 형식 (UI 표준)
모든 카드는 다음을 보여준다:
- **상태 배지** (정상/주의/위험)
- **한 줄 요약** (비개발자도 이해)
- **근거 1~2문장** (Agent 또는 시스템이 왜 그렇게 판단했는지)
- **액션 버튼** (있을 때만, 위험 액션은 admin 토큰 요구)

이 패턴이 모든 화면에서 반복되므로 사용자는 한 번 학습 후 어디서나 같은 방식으로 읽을 수 있다.

---

## 4. MVP 범위 — 포함

| 영역 | 포함 |
|---|---|
| 데이터 | Upbit/OKX 시세 (OHLCV, tick, orderbook, funding), USDT/KRW FX, 거래소 공지 |
| 전략 | 4대 전략 (Trend / Volatility Breakout / Pair / Kimp Mean Reversion), **signal-only** |
| AI Agent | MarketObserver / RiskOfficer / ExecutionRecommender / DailyReport (분석·추천·설명만) |
| 리스크 | RiskManager + OrderGuard + PermissionGate + KillSwitch + ApprovalQueue |
| 실행 | OrderGateway 단일 경로, PaperExecutor + ShadowExecutor |
| 모드 | SIMULATION, PAPER, LIVE_SHADOW (LIVE_MANUAL 코드는 있으나 기본 비활성) |
| UI | PC 관제 대시보드 + 모바일 핵심 조작 + GitHub Pages mock 데모 |
| 운영 | 감사 로그, Pre-market 체크리스트, DailyReport, 승격 게이트 |

---

## 5. MVP 제외 — 의도적으로 하지 않는다

| 영역 | 제외 이유 |
|---|---|
| 실제 LIVE 주문 송신 | LiveExecutor 의도적 미연결. 별도 PR + 별도 환경변수 + 사용자 승인 필요 (#27,#28,#59) |
| 출금 권한 사용 | 영구 금지. `ENABLE_WITHDRAWAL` 코드상 false 고정 |
| 선물/레버리지 LIVE | Phase 8 (체크리스트 #67-72) 후순위. simulation only |
| AI 자동 실행 (LIVE_AI_EXECUTION) | 정량 기준 + 사용자 옵트인 후에만 |
| 비정형 데이터 직접 주문 | 뉴스/트렌드/테마는 후보 필터로만, BUY/SELL 반환 금지 |
| 다거래소 전부 동시 지원 | Upbit + OKX 우선. Binance read-only 조사만 (#23) |
| 화려한 차트/지표 위주 UI | **상태/판단 근거 가시성** 우선. 차트는 보조 |
| 무인 24/7 실거래 운영 | 사용자 관제가 전제. 24/7 무인은 비목표 |

---

## 6. 승격 원칙 (Promotion)

```text
SIMULATION → PAPER → LIVE_SHADOW
                   → LIVE_MANUAL_APPROVAL
                   → LIVE_AI_ASSIST
                   → LIVE_AI_EXECUTION (옵트인)
```

각 승격은 **세 가지 모두 충족**해야 한다:

1. **정량 기준** — `app/governance/promotion_gates.py` 의 자동 게이트
   - PAPER → SHADOW: Sharpe ≥ 0.8, MDD ≤ 15%, 승률 ≥ 45%, 거래 ≥ 200, 운영 ≥ 4주
   - SHADOW → LIVE_MANUAL: Shadow ≥ 2주, P95 지연 ≤ 500ms, 장애 드릴 ≥ 4회
   - 그 외는 #65, #66, #59 항목에서 정의

2. **체크리스트 PASS** — 해당 단계 진입에 필요한 P0 항목 전부 완료

3. **사용자 명시적 승인** — 환경변수 변경 + 별도 문서 + 별도 테스트 통과 후 사람 승인

> **PASS는 실거래 허가가 아니다.** 정량 기준만 통과해도 사람 승인 없으면 LIVE 활성화하지 않는다.

승격 후에도 기준 미달 시 자동 강등 (`promotion_gates.check_reversion`).

---

## 7. AI Agent 원칙 (제품 차원)

- AI Agent는 **분석·추천·설명**만. **직접 주문 금지**.
- AgentDecision은 `is_order_intent=false` 가 기본값.
- 모든 Agent 판단은 **근거(reason)** 와 **신뢰도(confidence)** 를 동반.
- 낮은 confidence는 `WATCH_ONLY` (사용자에게 보여주되 액션 없음).
- RiskOfficerAgent는 **최종 거부권**. REJECT 시 어떤 주문 후보도 생성되지 않는다.

→ 자세한 원칙은 [`docs/safety_principles.md`](./safety_principles.md) + [`CLAUDE.md`](../CLAUDE.md).

---

## 8. 비목표 (Non-Goals)

명시적으로 하지 않는 것:
- 단기간 실거래 자동수익 — **시간이 걸리는 검증**이 본 시스템의 가치
- 차익거래 봇처럼 ms 단위 경쟁 — 안전·관측 가능성을 ms 속도보다 우선
- 모든 코인/모든 거래소/모든 전략 동시 지원 — Universe 20~100개로 제한 (#14)
- 일반 대중 SaaS — 사용자 1명 운용 기준. 멀티테넌시 비목표
- 모바일 앱 자체 거래 기능 — 모바일은 관제·승인 위주
- 24/7 무인 운영 — 사람이 매일 확인하는 것이 전제

---

## 9. 측정 가능한 성공 기준 (MVP Done)

본 MVP의 "끝"은 다음 모두 만족 시점:
- [ ] 4대 전략 PAPER 운영 4주 + 거래 200건 이상
- [ ] AgentOrchestrator + 4개 핵심 Agent (Observer/Risk/Execution/DailyReport) 동작
- [ ] OrderGateway 단일 경로로 모든 주문 라우팅, 우회 없음 (회귀 테스트 PASS)
- [ ] 감사 로그에서 지난 7일 모든 결정 재구성 가능
- [ ] PC 대시보드 + 모바일에서 핵심 카드 6개 사용 가능
- [ ] LIVE 모드 한 번도 활성화되지 않음 (의도된 결과)
- [ ] checklist_progress.md 의 P0 60항목 PASS

이 시점이 되면 `docs/mvp_completion.md` (#90) 에서 LIVE_SHADOW 진입 여부를 별도 결정한다.

---

## 10. 변경 이력

| 일자 | 변경 |
|---|---|
| 2026-05-10 | 체크리스트 #1 산출물로 본 문서 작성 (Step B 시작) |
