# News / Trend Agent — 체크리스트 #39

키워드 증가·뉴스 증가·공시 이벤트를 요약해 *테마 후보 발굴* 을 보조하는 Agent.
`backend/app/agents/news_trend_agent.py` 의 `NewsTrendAgent` 사양 문서이다.

본 Agent 는 #37 6-role Agent Architecture 의 `STRATEGY_RESEARCHER` role
specialization 이다.

> **본 단계 완료는 실거래 허가가 아니다 (CLAUDE.md §2.6 / §2.3).** NewsTrendAgent
> 는 *결론을 만들지 않으며* broker / adapter / OrderGateway 를 호출하지 않고
> 외부 뉴스 / 트렌드 / 공시 API 를 *직접 호출하지 않는다*. 출력에
> `direct_order_allowed=False` / `broker_call_allowed=False` /
> `used_for_order=False` 영구.

---

## 1. News / Trend Agent 목적

뉴스·트렌드·공시 데이터를 *조사* 해 테마 후보 발굴을 보조한다:

1. **키워드** 증가 추세 요약 — 어떤 키워드가 surge / grow / decline 인지.
2. **뉴스** 볼륨 추세 요약 — 시장 관심 정도.
3. **공시 / 거래소 공지** 이벤트 요약 — 상장폐지 / 유의종목 / 거래중단 / 입출금
   중단 등 *리스크 이벤트* 만 평탄화.
4. **테마 후보** 도출 — 관련 심볼·키워드·attention score·sentiment 평균·
   리스크 등급.
5. **테마 리스크** (hype / high_attention / negative_sentiment) 노트.

본 Agent 의 결과는 *StrategySelectionAgent* / *Report Writer* 가 참고할 context
일 뿐 *주문 명령이 아니다*.

---

## 2. 절대 하지 않는 것 (CLAUDE.md §2.3 / §3.1)

- 매수/매도/진입/청산 결론 0 (`recommendations = ()` 영구).
- broker / adapter / OrderGateway / MockBroker / PaperBroker 호출 0.
- `place_order` / `cancel_order` / `get_balance` / `submit_order` / `withdraw`
  / `deposit` / `set_leverage` / `set_margin` 호출 0.
- **외부 뉴스 / 트렌드 / 공시 API 직접 호출 0** — 데이터는 외부 collector (
  #18 `notice_collector.py` / #19 `theme_signals.py`) 가 *별도로* 수집한
  결과를 *입력으로만* 받는다.
- `ThemeCandidate.used_for_order = False` 영구.
- "BUY" / "SELL" / "ENTER" / "EXIT" 따옴표 리터럴 부재.
- `executable_order` / `order_request` / `broker_payload` /
  `place_order_payload` 출력 키 부재.

---

## 3. 키워드 증가 요약 방식

```text
growth_pct = (current_count - previous_count) / previous_count × 100
             (previous=0 시 None — 신규 키워드)

direction:
  growth >= surging_growth_pct (200)           → SURGING
  growth >= min_keyword_growth_pct (50)         → GROWING
  growth <= declining_growth_pct (-30)          → DECLINING
  None (신규)                                    → UNKNOWN
  그 외                                          → STABLE

필터: min_keyword_growth_pct 미달 제외. None (신규) 은 통과.
정렬: 신규 (None) 가 가장 위, 그다음 growth_pct 큰 순.
상한: top_keywords_limit (기본 20)
```

`related_symbols` 는 대문자 정규화 (`btc` → `BTC`).

---

## 4. 뉴스 증가 요약 방식

```text
news_volume.current     : 최근 window 뉴스 수
news_volume.previous    : 직전 window 뉴스 수
news_volume.by_source   : 소스별 카운트
news_volume.window_hours: lookback (기본 24)

growth_pct = (current - previous) / previous × 100
direction  : same classifier as keyword growth
```

---

## 5. 공시 / 거래소 공지 이벤트 요약 방식

우선 `payload["disclosures"]` 리스트 사용. 없으면 `payload["notice_context"]`
(#18 builder 결과) 의 `symbol_summaries` 로 fallback.

각 항목:

```python
{
  "exchange": "upbit",
  "symbol":   "XRP",     # 대문자 정규화
  "notice_type": "CAUTION" | "DELISTING" | ...,
  "severity":    "INFO" | "WARNING" | "HIGH" | "CRITICAL",
  "title":       str,
  "published_at": str | None,
  "risk_flag":   str | None
}
```

severity HIGH/CRITICAL 개수가 *risk note* 또는 *finding* 으로 노출된다.

---

## 6. 테마 후보 도출 / attention score 산출 방식

입력: `payload["theme_signals"]` — #19 `ThemeSignal` 호환 dict 시퀀스.

```python
{
  "theme": "ETF",
  "related_symbols": ["BTC"],
  "related_keywords": ["spot etf"],
  "score": 0.95,           # 0~1 또는 0~100
  "sentiment": 0.3,        # -1.0 ~ 1.0
  "sources": ["twitter"],
  "provider": "google_trends"  # 호환 키
}
```

알고리즘:

```text
정규화: score 가 [-1, 1] 범위면 ×100 환산, 0~100 범위는 그대로.
누적: 같은 theme 의 다중 signal 은 attention 누적 (score × 0.5 가산, cap=100,
      개별 최댓값 max_score 와 결합).
attention_score = clamp(0, 100, max(accumulator, max_score))

risk_level:
  attention_score >= hype_risk_threshold (90)      → HYPE
  attention_score >= high_attention_threshold (80) → HIGH_ATTENTION
  그 외                                              → NORMAL

sentiment_avg = mean(per-signal sentiment), None 가능

notes:
  HYPE / HIGH_ATTENTION         → "elevated attention — observe only, not an order signal"
  sentiment_avg <= -0.5         → "negative sentiment observed — review"

related_symbols / related_keywords / sources 모두 정렬된 tuple.
정렬: attention_score 내림차순.
상한: top_themes_limit (기본 10).
```

`ThemeCandidate.used_for_order = False` 영구.

---

## 7. 리스크 노트 / hype risk 처리 방식

`compute_theme_risk_notes(candidates)` 가 각 후보를 검사해 리스크 노트 생성:

| 조건 | code | severity |
|---|---|:---:|
| `risk_level == HYPE` | `hype_risk` | HIGH |
| `risk_level == HIGH_ATTENTION` | `high_attention` | WARNING |
| `sentiment_avg <= negative_sentiment_threshold (-0.5)` | `negative_sentiment` | WARNING |

리스크 노트는 *경고* 이며 *주문 명령이 아니다*. StrategySelectionAgent /
RiskAuditor 가 후속 검토에 참고.

---

## 8. JSON structured output 검증

```python
{
  "kind": "news_trend_agent_output",
  "role": "STRATEGY_RESEARCHER",
  "version": "v1",
  "generated_at": "2026-05-18T...Z",
  "summary": "News/Trend research: keywords=N, themes=M, ...",
  "has_data": true,
  "keyword_trends": [...],
  "news_volume": {...} | null,
  "disclosures": [...],
  "theme_candidates": [
    {"theme": "ETF", "attention_score": 95.0, "risk_level": "HYPE",
     "used_for_order": false, ...}, ...
  ],
  "risk_notes": [
    {"theme": "ETF", "code": "hype_risk", "severity": "HIGH", ...}, ...
  ],
  "findings": [...],

  "direct_order_allowed": false,    // 영구
  "broker_call_allowed":  false,    // 영구
  "used_for_order":       false     // 영구
}
```

데이터가 전혀 없으면 *insufficient_data* 안전 경로:

```python
{
  "summary": "insufficient_data — no news/trend context provided",
  "has_data": false,
  "findings": [{"kind": "insufficient_data", "severity": "WARNING", ...}],
  ...
}
```

JSON 직렬화 호환 — `to_json()` 가 `json.loads()` parse 가능.

---

## 9. MOCA 카드 (`AgentCard.to_dict()`)

```json
{
  "role": "STRATEGY_RESEARCHER",
  "title": "News / Trend Agent",
  "description": "키워드 증가·뉴스 증가·공시/거래소 공지 이벤트를 요약해 테마 후보 발굴을 보조…",
  "inputs": ["keywords", "news_volume", "disclosures", "theme_signals",
             "notice_context"],
  "outputs": ["keyword_trends", "news_volume_summary", "disclosure_events",
              "theme_candidates", "theme_risk_notes"],
  "forbidden_actions": ["execute_order", "invoke_broker", "invoke_order_gateway",
                        "write_order_request", "place_order", "cancel_order",
                        "get_balance", "fetch_external_news_api",
                        "fetch_external_trend_api"],
  "allowed_permissions": ["read_market_data", "read_notices",
                          "read_themes", "write_finding"],
  "direct_order_allowed": false,
  "can_invoke_broker": false,
  "can_invoke_order_gateway": false
}
```

`StructuredAgentRegistry().register(NewsTrendAgent())` 시 `validate_safety()`
자동 호출 — FORBIDDEN 권한 (8개) 교집합 0 검증 통과.

---

## 10. Agent 직접 주문 금지 (정적 회귀 카탈로그)

`backend/app/agents/news_trend_agent.py` 에 대해:

| 검사 | 결과 |
|---|:---:|
| `from app.brokers` / `app.execution` import | 부재 |
| `from app.order_gateway` / `app.adapters` / `app.broker` import | 부재 |
| network SDK (`requests`/`httpx`/`ccxt`/`pyupbit`/`binance`/`okx`) import | 부재 |
| `.place_order` / `.cancel_order` / `.get_balance` / `.submit_order` / `.withdraw` / `.deposit` / `.set_leverage` / `.set_margin` 호출 | 부재 |
| `ENABLE_LIVE_TRADING=True` / `direct_order_allowed=True` / `broker_call_allowed=True` / `used_for_order=True` / `is_executable=True` / `is_order_request=True` literal | 부재 |
| `"BUY"` / `"SELL"` / `"ENTER"` / `"EXIT"` quoted literal | 부재 |
| `"executable_order"` / `"order_request"` / `"broker_payload"` / `"place_order_payload"` output key | 부재 |
| 외부 뉴스 / 트렌드 / 공시 API 직접 호출 | 부재 (입력으로만 받음) |

---

## 11. 39번 완료는 실거래 허가가 아님 · 40번 이후 미작업

CLAUDE.md §2.6 — 본 단계 완료는 *실거래 허가가 아니다*.

- ENABLE_LIVE_TRADING / ENABLE_AI_EXECUTION / ENABLE_CRYPTO_FUTURES_LIVE 모두
  기본 false 유지.
- 본 단계는 *Strategy Researcher specialization* 만 제공. 40번 이후 Agent 기능
  은 본 작업 범위가 아니다.

---

## 12. 참조 모듈

- 구현: `backend/app/agents/news_trend_agent.py`
- 회귀: `backend/tests/test_news_trend_agent.py` (~60 케이스)
- 상위 Architecture (#37): `backend/app/agents/base.py` /
  `docs/agent_architecture.md`
- 입력 source (직접 호출 X — 수집 결과만 받음):
  - 거래소 공지 (#18): `backend/app/market/notice_collector.py` /
    `notice_context.py`
  - 테마 / 뉴스 / 트렌드 (#19): `backend/app/market/theme_signals.py`
- 안전 원칙: `docs/safety_principles.md` / `CLAUDE.md` §2.3 / §3.1.
