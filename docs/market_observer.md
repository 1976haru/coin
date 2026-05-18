# Market Observer Agent — 체크리스트 #38

장중 시장 환경을 *관찰* 하여 JSON structured output 으로 요약하는 Agent.
`backend/app/agents/market_observer.py` 의 `MarketObserverAgent` 사양 문서이다.

본 Agent 는 #37 6-role Agent Architecture 의 `OBSERVER` role specialization 이다.

> **본 단계 완료는 실거래 허가가 아니다 (CLAUDE.md §2.6 / §2.3).** Market Observer
> 는 *결론을 만들지 않으며* broker / adapter / OrderGateway / 외부 API 를
> 호출하지 않는다. `direct_order_allowed=False` / `broker_call_allowed=False`
> / `used_for_order=False` 영구. BUY / SELL / ENTER / EXIT 실행 action 부재.

---

## 1. MarketObserverAgent 역할

장중 시장 환경 *관찰자*. 다음 6개 영역을 관찰해 JSON 으로 요약한다:

1. **market breadth** — 시장 폭 (advancing / declining / A-D ratio / risk_tone)
2. **거래대금/거래량 흐름** — total volume / top volume symbols / surge count
3. **급등락 (top movers)** — change_pct 절댓값 상위 자산
4. **섹터/테마 흐름** — sector_map 우선, theme_context fallback
5. **변동성 (volatility regime)** — HIGH/LOW/NORMAL/UNKNOWN tone + transition risk
6. **freshness / data quality 헬스** — stale symbols / quality EXCLUDE 카운트

추가로 notice_context / theme_context / kimp_context / funding_context 를 그대로
*관찰 결과로 포함* (가공 없이 노출).

---

## 2. 절대 하지 않는 것 (CLAUDE.md §2.3 / §3.1)

- 매수/매도/진입/청산 결론 0 (`recommendations = ()` 영구).
- broker / adapter / OrderGateway / MockBroker / PaperBroker 호출 0.
- `place_order` / `cancel_order` / `get_balance` / `submit_order` / `withdraw` /
  `deposit` / `set_leverage` / `set_margin` 호출 0.
- 외부 API / 거래소 / 뉴스 / 트렌드 endpoint 직접 호출 0.
- 데이터 수집 직접 수행 0 — 입력 context 만 관찰.
- `executable_order` / `order_request` / `broker_payload` /
  `place_order_payload` 출력 키 부재.
- "BUY" / "SELL" / "ENTER" / "EXIT" 따옴표 리터럴 부재 (정적 회귀로 강제).

---

## 3. 입력 (`AgentInput.payload`)

| 키 | 타입 | 설명 |
|---|---|---|
| `market_context` | dict | tickers 리스트 + volatility_summary + sector_map + freshness_state + data_quality_summary |
| `theme_context` | dict | active themes + related_symbols + score (optional) |
| `notice_context` | dict | 거래소 공지 요약 (#18) |
| `kimp_context` | dict | 김프 계산 context (#34) — observe-only |
| `funding_context` | dict | 펀딩 가드 context (#36) — observe-only |

모든 입력은 *읽기 전용*. Agent 는 입력 수정 없이 관찰 결과만 생성한다.

### 3.1 `market_context.tickers` 항목

```python
{
    "symbol": "BTC",
    "change_pct": 2.5,       # %
    "volume": 1234567.0,     # 거래대금/거래량
    "avg_volume": 1000000.0  # 평균 거래대금 (surge 판정용)
}
```

### 3.2 `market_context.sector_map`

```python
{
    "L1": ["BTC", "ETH", "SOL"],
    "Meme": ["DOGE", "SHIB"],
    "AI": ["FET", "AGIX"]
}
```

`sector_map` 이 없으면 `theme_context.themes[].related_symbols` 로 fallback.

### 3.3 `market_context.volatility_summary`

선택적. 있으면 우선 사용 — 없으면 ticker change_pct 분산으로 fallback 계산.

```python
{
    "avg_volatility": 2.5,
    "volatility_tone": "NORMAL",  # HIGH_VOLATILITY / LOW_VOLATILITY / NORMAL / UNKNOWN
    "high_volatility_symbols": ["BTC"],
    "transition_risk": false
}
```

### 3.4 `market_context.freshness_state`

```python
{"ok": True, "stale_symbols": []}
# 또는 단순 bool: True
```

### 3.5 `market_context.data_quality_summary`

```python
{"grade": "GOOD", "exclude_count": 0}
```

---

## 4. 출력 — `MarketObserverOutput`

```python
{
  "kind": "market_observer_output",
  "role": "OBSERVER",
  "version": "v1",
  "generated_at": "2026-05-18T...Z",
  "summary": "Market environment observation: breadth=RISK_ON, volatility=NORMAL, ...",
  "has_data": true,
  "market_breadth": {
    "total_symbols": 10, "advancing_count": 6, "declining_count": 3, ...,
    "risk_tone": "RISK_ON"
  },
  "volume_flow": {"total_volume": 1234.5, "top_volume_symbols": ["..."],
                  "surge_count": 2},
  "top_movers": [{"symbol": "BTC", "change_pct": 5.0, "direction": "UP"}, ...],
  "sector_flows": [{"sector": "L1", "tone": "STRONG", ...}],
  "volatility_regime": {"avg_volatility": 2.5, "volatility_tone": "NORMAL",
                        "transition_risk": false},
  "data_health": {"freshness_ok": true, "stale_symbols": [],
                  "data_quality_grade": "GOOD"},
  "notice_observation": {"total_notices": 2, "high_risk_symbols": ["XRP"]},
  "theme_observation": {"active_theme_count": 3, "human_summary": "..."},
  "kimp_context": {...},        # 입력 그대로
  "funding_context": {...},     # 입력 그대로
  "findings": [...],            # AgentFinding 평탄 리스트

  "direct_order_allowed": false,    // 영구 False
  "broker_call_allowed": false,     // 영구 False
  "used_for_order": false           // 영구 False
}
```

데이터가 전혀 없으면 *insufficient_data* 안전 경로로 떨어져:

```python
{
  "summary": "insufficient_data — no market/theme/notice context provided",
  "has_data": false,
  "findings": [{"kind": "insufficient_data", "severity": "WARNING", ...}],
  ...
}
```

---

## 5. market breadth 요약 방식

```text
advancing = | t : change_pct > 0 |
declining = | t : change_pct < 0 |
A/D ratio = advancing / declining  (declining 0 시 None)

risk_tone:
  declining_share >= 0.6  → RISK_OFF
  advancing_share >= 0.6  → RISK_ON
  데이터 부족             → UNKNOWN
  그 외                   → MIXED
```

`avg_change_pct` / `median_change_pct` 도 함께 계산.

---

## 6. 거래대금/거래량 흐름 요약 방식

```text
total_volume       = Σ ticker.volume
avg_volume_per_symbol = total_volume / N
top_volume_symbols = top 5 by ticker.volume
surge_count        = | t : volume / avg_volume >= 2.0 |   (기본 임계)
```

`surge_threshold_ratio` 는 config 로 조정 가능.

---

## 7. 급등락 / top movers 감지 방식

- ticker `change_pct` 의 *절댓값* 으로 정렬, top N (기본 5) 반환.
- `abs_change_threshold_pct` 미달 자산 제외.
- 각 mover 의 `direction` = "UP" / "DOWN".
- *주문 명령이 아님* — 단순 관찰 결과.

---

## 8. 섹터/테마 흐름 요약 방식

1. `market_context.sector_map` 이 있으면 섹터별 ticker 집계.
2. 없으면 `theme_context.themes[].related_symbols` 로 최소 요약.
3. `avg_change_pct` 로 tone 분류:
   - `>= +1.0%`  → STRONG
   - `<= -1.0%`  → WEAK
   - 그 사이     → MIXED
   - 데이터 부족 → UNKNOWN
4. `theme_score >= 0.8` 이면 notes 에 *"theme score elevated — observe only,
   not an order signal"* 자동 추가.
5. theme score 는 *주문 신호로 사용하지 않는다*.

---

## 9. volatility / data health 요약 방식

### 9.1 volatility regime

- `market_context.volatility_summary` 가 있으면 우선 사용.
- 없으면 ticker `|change_pct|` 평균으로 proxy:
  - `>= 3.0%`  → HIGH_VOLATILITY
  - `<= 0.5%`  → LOW_VOLATILITY
  - 그 사이     → NORMAL
  - 데이터 부족 → UNKNOWN
- `transition_risk = True` 조건: HIGH_VOLATILITY + sharp drop 개수가 전체의
  1/4 이상.
- 결과는 StrategySelectionAgent 가 *참고* 할 context 일 뿐 전략 선택을 확정하지
  않는다.

### 9.2 data health

- `freshness_ok` — `market_context.freshness_state.ok` 또는 단순 bool.
- `stale_symbols` — stale 항목 평탄화.
- `data_quality_grade` + `quality_excluded_count` — `data_quality_summary` 매핑.

---

## 10. MOCA 카드 (`AgentCard.to_dict()`)

```json
{
  "role": "OBSERVER",
  "title": "Market Observer Agent",
  "description": "시장지수·거래대금·급등락·섹터 흐름·변동성·freshness/data quality·notice·theme 을 관찰…",
  "inputs": ["market_context", "theme_context", "notice_context",
             "kimp_context", "funding_context"],
  "outputs": ["market_breadth", "volume_flow", "top_movers", "sector_flows",
              "volatility_regime", "data_health", "notice_observation",
              "theme_observation"],
  "forbidden_actions": ["execute_order", "invoke_broker", "invoke_order_gateway",
                        "write_order_request", "place_order", "cancel_order",
                        "get_balance", "fetch_external_api",
                        "collect_market_data"],
  "allowed_permissions": ["read_data_quality", "read_freshness", "read_funding",
                          "read_kimp", "read_market_data", "read_notices",
                          "read_themes", "write_finding"],
  "direct_order_allowed": false,
  "can_invoke_broker": false,
  "can_invoke_order_gateway": false
}
```

`StructuredAgentRegistry` 에 등록 시 `validate_safety()` 가 자동 검사 — FORBIDDEN
권한 (8개) 교집합 시 `AgentSafetyViolation` raise.

---

## 11. JSON structured output 검증

테스트가 자동 회귀:

- `to_dict()` / `to_json()` 가 정상 직렬화 (`json.loads(to_json())` parse 가능).
- 평탄 dict — 중첩 dataclass 도 dict 로 변환.
- Decimal / datetime 은 str 직렬화 (`default=str`).
- `direct_order_allowed=False`, `broker_call_allowed=False`,
  `used_for_order=False` 모두 출력에 포함.
- "BUY" / "SELL" / "ENTER" / "EXIT" 토큰 누설 0 (`\bBUY\b` 등 검사).

---

## 12. Agent 직접 주문 금지 (정적 회귀 카탈로그)

`backend/app/agents/market_observer.py` 에 대해 다음 정적 회귀:

| 검사 | 결과 |
|---|:---:|
| `from app.brokers` / `app.execution` import | 부재 |
| `from app.order_gateway` / `app.adapters` / `app.broker` import | 부재 |
| network SDK (`requests`/`httpx`/`ccxt`/`pyupbit`/`binance`/`okx`) import | 부재 |
| `.place_order` / `.cancel_order` / `.get_balance` / `.submit_order` / `.withdraw` / `.deposit` / `.set_leverage` / `.set_margin` 호출 | 부재 |
| `ENABLE_LIVE_TRADING=True` / `direct_order_allowed=True` / `broker_call_allowed=True` / `used_for_order=True` 등 literal | 부재 |
| `"BUY"` / `"SELL"` / `"ENTER"` / `"EXIT"` quoted literal | 부재 |
| `"executable_order"` / `"order_request"` / `"broker_payload"` / `"place_order_payload"` output key | 부재 |

---

## 13. 후속 단계 / 38번 완료는 실거래 허가가 아님

CLAUDE.md §2.6 — 본 단계 완료는 *실거래 허가가 아니다*.

- ENABLE_LIVE_TRADING / ENABLE_AI_EXECUTION / ENABLE_CRYPTO_FUTURES_LIVE 모두
  기본 false 유지.
- 본 단계는 *관찰자* 만 제공. 39번 이후 본격 Agent (SignalQuality 확장 / Risk
  Officer 본격 통합 / StrategySelection 본격 / API endpoint / UI 카드) 는 본
  작업 범위가 아니다.

---

## 14. 참조 모듈

- 구현: `backend/app/agents/market_observer.py`
- 회귀: `backend/tests/test_market_observer.py` (53 케이스)
- 상위 Architecture (#37): `backend/app/agents/base.py` /
  `docs/agent_architecture.md`
- 입력 출처:
  - 시장 데이터 (#15): `backend/app/market/collector.py`
  - freshness (#16): `backend/app/market/freshness.py`
  - data quality (#17): `backend/app/market/data_quality.py`
  - 거래소 공지 (#18): `backend/app/market/notice_context.py`
  - theme signals (#19): `backend/app/market/theme_signals.py`
  - kimp (#34): `backend/app/market/kimp_calculator.py`
  - funding (#36): `backend/app/risk/funding.py`
- 안전 원칙: `docs/safety_principles.md` / `CLAUDE.md` §2.3 / §2.4 / §3.1.
