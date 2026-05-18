# Agent Architecture — 체크리스트 #37

Agent Trader Crypto OS v1 의 *Agent 역할 분리* 사양. 본 문서는
`backend/app/agents/base.py` 의 6-role 구조적 layer 를 설명한다. 기존 4-agent
구현 (Anomaly / SignalQuality / RiskOfficer / Orchestrator) 은 본 layer 아래에서
그대로 동작하며 회귀가 보호된다.

> **본 단계 완료는 실거래 허가가 아니다 (CLAUDE.md §2.6 / §2.3).** Agent 는 분석
> / 추천 / 설명만 한다. 직접 주문 금지. broker / adapter / OrderGateway 직접
> 호출 금지. 본 문서의 라벨은 *주문 명령이 아니다*.

---

## 1. Agent Architecture 목적

Agent 가 *한 번에 관찰→분석→리스크→주문* 까지 수행하면 위험하다 (이노그릿
실패 교훈). 따라서:

- 역할을 6개로 분리한다.
- 각 Agent 는 자신의 `role_scope` 안에서만 output 을 생성한다.
- 모든 Agent 의 output 은 *JSON structured* — `recommendation` / `context` /
  `reasoning summary` 일 뿐 실제 주문 명령이 아니다.
- 실제 주문 전환은 후속 *단일 경로* (RiskManager → OrderGuard → PermissionGate
  → ApprovalQueue → OrderGateway) 에서만 가능하다.

---

## 2. 왜 역할을 분리하는가

- *책임 분리* — 한 Agent 가 결정 + 실행을 동시에 하지 않도록 강제.
- *감사 가능성* — 각 단계 output 이 분리되어 audit log 가 명확.
- *부분 신뢰* — 일부 Agent (Observer / Analyst) 만 활성화하고 Execution
  Recommender 는 비활성으로 둘 수 있다.
- *권한 최소화* — 각 역할이 받을 수 있는 ``AgentPermission`` 카탈로그가 제한되며,
  *FORBIDDEN 권한* (`execute_order` / `invoke_broker` / `invoke_order_gateway` 등)
  은 어떤 Agent 에도 부여되지 않는다.
- *UI 표시* — MOCA 모듈 카드처럼 각 역할의 입력/출력/금지 행동을 한 눈에 본다.

---

## 3. Observer 역할

| 항목 | 값 |
|---|---|
| role | `OBSERVER` |
| title | Observer Agent |
| 역할 | 시장 데이터·freshness·data_quality·notices·theme_context 관찰 |
| 결론 | **만들지 않는다** (관찰 요약만) |
| inputs | `market_data`, `freshness_state`, `data_quality_grade`, `notice_context`, `theme_context` |
| outputs | `observation_summary`, `observed_findings` |
| forbidden_actions | `execute_order`, `invoke_broker`, `invoke_order_gateway`, `write_order_request`, `build_recommendation` |
| allowed_permissions | `read_market_data`, `read_freshness`, `read_data_quality`, `read_notices`, `read_themes`, `write_finding` |
| direct_order_allowed | False (영구) |

---

## 4. Analyst 역할

| 항목 | 값 |
|---|---|
| role | `ANALYST` |
| title | Analyst Agent |
| 역할 | 전략 Signal·시장 상태·지표 분석. 후보의 장단점/근거 요약 |
| 결론 | 분석 finding 만, **권고/주문 생성 안 함** |
| inputs | `strategy_signal`, `regime`, `indicators`, `kimp_result` |
| outputs | `analysis_findings`, `candidate_summary` |
| forbidden_actions | `execute_order`, `invoke_broker`, `invoke_order_gateway`, `write_order_request` |
| allowed_permissions | `read_market_data`, `read_kimp`, `read_strategy_catalog`, `write_finding` |
| direct_order_allowed | False (영구) |

---

## 5. Risk Auditor 역할

| 항목 | 값 |
|---|---|
| role | `RISK_AUDITOR` |
| title | Risk Auditor Agent |
| 역할 | 리스크·stale data·data quality·funding cost·kimp guards·permission 상태 감사 |
| 결론 | `blocked_by` / `review_codes` 산출, **주문 명령 만들지 않음** |
| inputs | `freshness_state`, `data_quality_grade`, `kimp_guard_decision`, `funding_guard_decision`, `permission_state` |
| outputs | `risk_findings`, `blocked_by`, `review_codes` |
| forbidden_actions | `execute_order`, `invoke_broker`, `invoke_order_gateway`, `write_order_request`, `place_order` |
| allowed_permissions | `read_freshness`, `read_data_quality`, `read_kimp`, `read_funding`, `read_risk_state`, `write_finding` |
| direct_order_allowed | False (영구) |

#35 KimpRiskGuards 와 #36 FundingCostGuard 의 `KimpGuardDecision`/`FundingGuardDecision`
을 직접 입력으로 받아 finding 으로 평탄화한다.

---

## 6. Strategy Researcher 역할

| 항목 | 값 |
|---|---|
| role | `STRATEGY_RESEARCHER` |
| title | Strategy Researcher Agent |
| 역할 | 전략별 성능·장세 적합성·후보 전략 조사 |
| 결론 | regime/카탈로그 finding, **주문 명령 만들지 않음** |
| inputs | `regime`, `strategy_catalog`, `performance_history` |
| outputs | `strategy_candidates`, `regime_fit_findings` |
| forbidden_actions | `execute_order`, `invoke_broker`, `invoke_order_gateway`, `write_order_request` |
| allowed_permissions | `read_strategy_catalog`, `read_performance_history`, `read_market_data`, `write_finding` |
| direct_order_allowed | False (영구) |

후속 `StrategySelectionAgent` 가 이 context 를 받아 활성 전략 후보를 결정한다.

---

## 7. Report Writer 역할

| 항목 | 값 |
|---|---|
| role | `REPORT_WRITER` |
| title | Report Writer Agent |
| 역할 | 사람이 읽을 수 있는 보고서 / 로그 요약 |
| 결론 | report recommendation (`requires_review=True`), **주문 지시가 아님** |
| inputs | `findings_bundle`, `audit_log`, `performance_history` |
| outputs | `human_readable_report` |
| forbidden_actions | `execute_order`, `invoke_broker`, `invoke_order_gateway`, `write_order_request` |
| allowed_permissions | `read_performance_history`, `write_report` |
| direct_order_allowed | False (영구) |

---

## 8. Execution Recommender 역할

| 항목 | 값 |
|---|---|
| role | `EXECUTION_RECOMMENDER` |
| title | Execution Recommender Agent |
| 역할 | 실행 후보에 대한 *권고* 만 만든다. **직접 주문하지 않는다** |
| 결론 | `execution_recommendation` (`is_order_request=False` 영구, `requires_review=True`) |
| inputs | `candidate_summary`, `risk_findings`, `kimp_guard_decision`, `funding_guard_decision` |
| outputs | `execution_recommendation` |
| forbidden_actions | `execute_order`, `invoke_broker`, `invoke_order_gateway`, `write_order_request`, `place_order`, `cancel_order`, `get_balance` |
| allowed_permissions | `read_kimp`, `read_funding`, `read_risk_state`, `write_recommendation` |
| direct_order_allowed | False (영구) |

> Execution Recommender 의 output 은 *executable order 가 아니다*. 최종 실행은
> 후속 Risk / OrderGuard / PermissionGate / ApprovalQueue / OrderGateway 경로
> 에서만 가능 (§14).

---

## 9. 각 role 별 input / output (Python 구조)

```python
class AgentInput(frozen):
    role: str
    task: str
    payload: Mapping[str, Any]   # JSON 직렬화 가능

class AgentFinding(frozen):
    kind: str
    severity: "INFO" | "WARNING" | "HIGH" | "CRITICAL"
    message: str
    evidence: Mapping[str, Any]

class AgentRecommendation(frozen):
    kind: str
    summary: str
    evidence: Mapping[str, Any]
    requires_review: bool = True
    is_order_request: bool = False   # 영구 False

class AgentDecision(frozen):
    role: str
    summary: str
    findings: tuple[AgentFinding, ...]
    recommendations: tuple[AgentRecommendation, ...]
    is_executable: bool = False      # 영구 False

class AgentOutput(frozen):
    role: str
    version: str
    generated_at: datetime
    decision: AgentDecision
    direct_order_allowed: bool = False  # 영구 False
    used_for_order: bool = False        # 영구 False
```

---

## 10. 각 role 별 forbidden actions

모든 6 카드는 *최소한* 다음 forbidden actions 를 카드 메타데이터로 명시한다 (정적
회귀 테스트로 강제):

- `execute_order`
- `invoke_broker`
- `invoke_order_gateway`

추가로 역할별로 (`write_order_request` / `place_order` / `cancel_order` /
`get_balance` 등) 을 명시한다.

또한 `AgentPermission` 의 FORBIDDEN 카탈로그 (8개) 는 어떤 Agent 카드에도
`allowed_permissions` 로 들어가지 않는다:

```
EXECUTE_ORDER, INVOKE_BROKER, INVOKE_ORDER_GATEWAY, READ_SECRETS,
WRITE_ORDER_REQUEST, PLACE_ORDER_PERMISSION, CANCEL_ORDER_PERMISSION,
GET_BALANCE_PERMISSION
```

`StructuredAgentBase.validate_safety()` 가 매 등록 시 검사하며 위반 시
`AgentSafetyViolation` raise.

---

## 11. JSON structured output 규칙

모든 Agent 는 `AgentOutput.to_dict() / to_json()` 으로 JSON 직렬화한다.

- 최상위 키: `role` / `version` / `generated_at` (ISO8601) / `decision` /
  `direct_order_allowed=False` / `used_for_order=False`.
- `decision.findings` 와 `decision.recommendations` 는 평탄 리스트.
- `decision.is_executable=False` 명시.
- Decimal/datetime 은 str 직렬화 (정밀도 보존).

---

## 12. Agent 는 broker / adapter / OrderGateway 직접 호출 금지

`backend/app/agents/base.py` 는 다음 정적 회귀로 강제:

- `from app.brokers` / `app.execution` import 부재
- `from app.order_gateway` / `app.adapters` / `app.broker` import 부재
- 네트워크 SDK (`requests`/`httpx`/`ccxt`/`pyupbit`/`binance`/`okx`) import 부재
- `.place_order` / `.cancel_order` / `.get_balance` / `.submit_order` /
  `.withdraw` / `.deposit` / `.set_leverage` / `.set_margin` 호출 부재
- forbidden literal 부재: `ENABLE_LIVE_TRADING=True`, `is_executable=True`,
  `is_order_request=True`, `direct_order_allowed=True`, `used_for_order=True`
- forbidden output key literal 부재: `"executable_order"`, `"order_request"`,
  `"broker_payload"`, `"place_order_payload"`

> 참고: `app/agents/compliance.py` 는 *broker 모듈을 audit 하기 위해* lazy
> import 를 한다 — 본 정적 회귀의 범위는 `base.py` 로 한정한다.

---

## 13. Execution Recommender 도 직접 주문 금지

Execution Recommender Agent 의 output 은 *권고* 이며 *실행* 이 아니다:

- `AgentRecommendation.is_order_request = False` 영구.
- `AgentDecision.is_executable = False` 영구.
- `AgentOutput.direct_order_allowed = False` 영구.
- 카드 `forbidden_actions` 에 `execute_order` / `place_order` / `cancel_order`
  / `get_balance` 모두 명시.
- 실제 실행은 §14 의 단일 경로에서만 가능.

---

## 14. 실제 주문 경로 (단일 경로 — 우회 금지)

```text
Strategy Signal
  → Agent context / recommendation   (Observer / Analyst / Risk Auditor /
                                     Strategy Researcher / Report Writer /
                                     Execution Recommender)
  → RiskManager
  → OrderGuard
  → PermissionGate
  → ApprovalQueue
  → OrderGateway
  → PaperExecutor / ShadowExecutor / (Future) Live Executor
```

CLAUDE.md §2.4 — 위 경로를 우회하는 새 코드는 작성 금지. Agent 어떤 단계도
PaperExecutor / OrderGateway 를 *직접 호출하지 않는다*.

---

## 15. MOCA 모듈 카드 표시 예시

각 Agent 의 `AgentCard.to_dict()` 가 UI 카드 1장의 데이터 모델이다. 예 (Observer):

```json
{
  "role": "OBSERVER",
  "title": "Observer Agent",
  "description": "시장 데이터·freshness·data_quality·notices·theme_context …",
  "inputs": ["market_data", "freshness_state", "data_quality_grade",
             "notice_context", "theme_context"],
  "outputs": ["observation_summary", "observed_findings"],
  "forbidden_actions": ["execute_order", "invoke_broker",
                        "invoke_order_gateway", "write_order_request",
                        "build_recommendation"],
  "allowed_permissions": ["read_data_quality", "read_freshness",
                          "read_market_data", "read_notices",
                          "read_themes", "write_finding"],
  "direct_order_allowed": false,
  "can_invoke_broker": false,
  "can_invoke_order_gateway": false
}
```

`StructuredAgentRegistry.catalog()` 는 6장 카드 dict 의 리스트를 반환한다 — UI/
API 가 그대로 받아 MOCA 카드 그리드에 렌더링 가능.

---

## 16. 37번 완료는 실거래 허가가 아님

CLAUDE.md §2.6 — 체크리스트 PASS 는 실거래 허가가 아니다.

- ENABLE_LIVE_TRADING / ENABLE_AI_EXECUTION / ENABLE_CRYPTO_FUTURES_LIVE 모두
  기본 false 유지.
- 본 단계는 *역할 분리 + 안전 정책 + JSON output contract* 기반만 제공.
- 실제 LLM 추론 / 본격 Agent 로직 / 메모리 / 에이전트 간 협업 / Streaming /
  /api/agents/architecture 엔드포인트 등은 후속 단계 (#38 이상) 의 범위다.

---

## 17. 38번 이후 Agent 기능은 이번 범위가 아님

- #38 Risk Officer Agent — 본격 audit 로직, kill_switch 연계.
- #39 Signal Quality Agent / #40 Anomaly Agent / #41 Explain Agent /
  #42 Daily Report Agent / #43 Theme Insight Agent — 각 후속 단계에서
  본 6-role base 위에 구현.
- LLM 강화 (`ENABLE_AI_AGENTS=true`) 는 별도 PR 에서 도입하고 모든 LLM 출력도
  `AgentOutput` schema 를 따라야 한다.

---

## 참조 모듈

- 구현: `backend/app/agents/base.py` (#37 — 기존 4-agent layer + 6-role
  Architecture layer 공존)
- 회귀: `backend/tests/test_agent_architecture_v2.py` (6-role layer)
- 기존 회귀: `backend/tests/test_agent_architecture.py` (4-agent layer)
- 가드 연동: `backend/app/strategies/kimp_risk_guards.py` (#35) /
  `backend/app/risk/funding.py` (#36)
- 안전 원칙: `docs/safety_principles.md` / `CLAUDE.md`
