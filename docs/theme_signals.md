# Trend/News/Theme Signals (체크리스트 #19)

> Agent Trader Crypto OS v1 — 비정형 데이터 후보 필터/테마 context 계층

## 1. 목적과 범위

Trend/News/Theme Signals 는 **구글트렌드/뉴스/공시/테마** 등 비정형 외부 데이터를
read-only 로 수집·정규화·중복제거·DB 저장하고, NewsTrendAgent / 후보 필터 / UI 가
사용할 수 있는 read-only context 계층이다.

본 계층은 다음만 한다.

- trend / news / disclosure / theme / macro_fx 등 비정형 source 의 텍스트/메타 수집
- 정규화 + risk_flag 추론 (화이트리스트 기반) + 중복 제거
- `theme_signals` 테이블에 영속화
- 후보 필터에 `candidate_filter_ok` / `candidate_filter_review_required` 라벨 부여
- NewsTrendAgent / ThemeInsightAgent 가 사용할 요약 context 생성

본 계층은 다음을 **하지 않는다**.

- BUY/SELL/ENTER/EXIT/LONG/SHORT 같은 매매 action 반환 ❌
- 거래소 LIVE 주문 / private endpoint 호출 ❌
- 출금 권한 키 사용 ❌
- 전체 시장 자동 스캔 ❌
- AI Agent 가 본 데이터로 직접 주문 ❌
- 실제 Google Trends / 뉴스 / 공시 API 호출 (mock provider 만, 실제 adapter 는 후속) ❌

`used_for_order` / `direct_order_allowed` 는 DB / API / Agent context 어디서나 영구
`False`. action 토큰은 응답 페이로드 어디에도 등장하지 않으며 정적·동적 회귀 테스트로
강제된다 (CLAUDE.md §2.3, §2.5).

## 2. 데이터 모델

### 2.1 `theme_signals` 테이블 (`ThemeSignal` ORM)

| 컬럼 | 타입 | 비고 |
|---|---|---|
| `id` | int PK | |
| `source` | string(32) | trend / news / disclosure / theme / macro_fx / other |
| `provider` | string(64) | mock_trend / mock_news / mock_disclosure / mock_theme / mock_macro / ... |
| `signal_id` | string(128) nullable | provider 측 식별자 (dedup 1순위) |
| `theme` | string(64) | ETF / AI / Layer2 / RWA / Regulation / Delisting / Exchange Risk / Macro / Hype / ... |
| `title` | text | strip + 최대 512자 |
| `summary` | text | 본문 (최대 4096자) |
| `url` | text | optional |
| `related_symbols` | JSON list[str] | upper-case, 정렬, dedup |
| `related_keywords` | JSON list[str] | 보조 키워드 |
| `score` | float nullable | 0.0~1.0 정규화 |
| `sentiment` | float nullable | -1.0~1.0 |
| `risk_flags` | JSON list[str] | 화이트리스트 부분집합 (§3) |
| `published_at` | datetime tz nullable | provider 가 준 게시 시각 |
| `collected_at` | datetime tz | 수집기가 본 시각 |
| `content_hash` | sha256(hex) | source/provider/title/summary 기반 |
| `used_for_order` | bool | **영구 False** (advisory 도 아님) |
| `direct_order_allowed` | bool | **영구 False** |
| `note` | text | |
| `raw_payload` | JSON | provider 원본 dict (secret 미포함) |
| `updated_at` | datetime tz | onupdate |

#### Unique 제약 (중복 제거 키)

1. `(source, provider, signal_id)` — `signal_id` 가 있을 때 우선 매칭.
2. `(source, provider, content_hash)` — `signal_id` 부재 시 본문 기반 매칭.

같은 신호가 두 번 수집되어도 row 가 중복되지 않으며, 새 본문은 기존 row 를 update.

#### CoinSignal 과의 분리

`CoinSignal` 은 가격/지표 기반 *advisory* 전략 신호 (used_for_order 컬럼이 OrderGateway
사용 시 True 로 갱신). `ThemeSignal` 은 비정형 외부 데이터의 context 전용이며 어떤
조건에서도 `used_for_order` 가 True 가 되지 않는다.

### 2.2 Alembic 마이그레이션

`backend/app/db/migrations/versions/0005_theme_signals.py` — `0004_exchange_notices`
다음 revision. downgrade 안전.

## 3. risk_flag 화이트리스트

본 계층의 정규화 결과 `risk_flags` 는 아래 8개 토큰의 부분집합만 사용한다. action
토큰(BUY/SELL/ENTER/EXIT/LONG/SHORT) 은 절대 포함되지 않으며, `infer_risk_flags` 의
화이트리스트 강제 + `normalize_signal` 의 명시적 차단 + 회귀 테스트 3개로 보장된다.

| flag | 트리거 |
|---|---|
| `high_news_attention` | breaking/긴급/exclusive 키워드 + score ≥ 0.8 |
| `regulatory_attention` | SEC/규제/regulation/compliance/조사 |
| `exchange_risk_attention` | exchange risk/withdrawal suspension/거래소 해킹 |
| `delisting_related_theme` | delisting/상장폐지/거래지원 종료 |
| `suspicious_hype_theme` | rug/scam/ponzi/hype |
| `macro_fx_attention` | FOMC/환율/금리/FX |
| `review_required` | sentiment ≤ -0.5 또는 다른 review-triggering 플래그 |
| `context_only` | 기본 fallback (위 조건 모두 미충족) |

## 4. ThemeProvider / MockThemeProvider

### 4.1 Protocol

```python
class ThemeProvider(Protocol):
    name: str

    def fetch_signals(
        self,
        since: datetime | None = None,
    ) -> list[RawThemeSignal]: ...
```

### 4.2 `MockThemeProvider`

외부 네트워크 호출 없는 결정론적 provider. 9개 fixture 가 trend / news / disclosure /
theme(AI, Layer2, RWA) / macro_fx / exchange risk / hype 를 커버하고, 일부는
`signal_id` 없이 `content_hash` dedup 검증용으로 설계되어 있다. 모든 fixture 본문에
"Mock fixture — 실제 사건 아님" 또는 유사 문구를 포함해 실제 투자 추천처럼 보이지
않도록 한다.

### 4.3 실제 외부 adapter

본 단계 범위 밖 — 후속 PR에서 read-only Google Trends / 뉴스 / 공시 adapter 를 같은
Protocol 로 구현해 collector 에 주입한다. 본 collector 는 어떤 외부 API SDK / 거래소
endpoint / 주문 endpoint 도 import 하지 않는다.

## 5. ThemeSignalCollector

```python
from app.db import session_scope
from app.market.theme_signals import ThemeSignalCollector, MockThemeProvider

c = ThemeSignalCollector({"mock": MockThemeProvider()})
with session_scope() as s:
    r = c.collect_once(s, provider_name="mock")
print(r.fetched, r.inserted, r.updated, r.skipped, r.by_source, r.by_risk_flag)
```

upsert 규칙: `(source, provider, signal_id)` 우선 → 없으면 `(source, provider,
content_hash)`. `used_for_order` / `direct_order_allowed` 는 항상 False 로 기록.

## 6. ThemeFilter (후보 필터)

```python
from app.market.theme_context import ThemeFilter

with session_scope() as s:
    out = ThemeFilter(s).annotate_candidates(
        [("LUNA", "upbit"), ("BTC", "upbit")],
        lookback_hours=72,
    )
for e in out:
    print(e.symbol, e.recommendation, list(e.risk_flags))
```

응답 필드(예시):
```jsonc
{
  "symbol": "LUNA",
  "exchange": "upbit",
  "themes": ["Delisting"],
  "risk_flags": ["delisting_related_theme", "review_required"],
  "recommendation": "candidate_filter_review_required",
  "risk_notes": ["[disclosure] Delisting (mock_disclosure): ..."],
  "used_for_order": false,
  "direct_order_allowed": false
}
```

`recommendation` 는 두 값만 사용: `candidate_filter_ok` / `candidate_filter_review_required`.

## 7. ThemeContextBuilder (NewsTrendAgent context)

```python
from app.market.theme_context import ThemeContextBuilder

with session_scope() as s:
    ctx = ThemeContextBuilder(s).build_theme_context(
        symbols=["BTC", "LUNA"],
        lookback_hours=72,
    )
print(ctx.to_dict())
```

응답 구조 요약:
```jsonc
{
  "generated_at": "...",
  "lookback_hours": 72,
  "total_signals": 9,
  "by_source": {"trend": 1, "news": 3, "disclosure": 1, "theme": 3, "macro_fx": 1},
  "by_theme": {"ETF": 1, "Regulation": 1, "Delisting": 1, "...": 0},
  "by_risk_flag": {"regulatory_attention": 1, "delisting_related_theme": 1, "...": 0},
  "high_attention_themes": [],
  "review_required_symbols": ["ETH", "LUNA", "XRP"],
  "symbol_summaries": [
    {
      "symbol": "LUNA",
      "themes": ["Delisting"],
      "risk_flags": ["delisting_related_theme", "review_required"],
      "signal_count": 1,
      "high_attention_count": 0,
      "sentiment_avg": -0.8,
      "recommendation": "candidate_filter_review_required",
      "used_for_order": false,
      "direct_order_allowed": false
    }
  ],
  "recent_titles": ["..."],
  "human_summary": "최근 72시간 내 theme signal 9건 수집. ... 본 정보는 후보 필터/리스크 설명용이며, 직접 매매 신호가 아닙니다.",
  "candidate_filter_flags": ["delisting_related_theme", "macro_fx_attention", "..."],
  "risk_notes": ["[news] Regulation (mock_news): ..."],
  "used_for_order": false,
  "direct_order_allowed": false
}
```

**핵심 — 본 context 는 후보 필터/리스크 설명 용도다.** `action`, `side`, `BUY`, `SELL`
같은 매매 토큰은 응답 어디에도 등장하지 않는다 (`_assert_no_action_tokens` 내장 가드).

## 8. NewsTrendAgent context 연결

NewsTrendAgent 가 별도로 존재하지 않으므로 본 단계에서는 새 대형 agent 를 만들지
않았다. `ThemeInsightAgent.decide()` 는 기존 거래 결정을 변경하지 않고
`ctx["theme_context"]` (ThemeContextBuilder 결과 dict) 가 있을 때만 `explain_text` 에
부가 정보로 첨부한다.

```python
ctx = {
    "symbol": "LUNA",
    "exchange": "upbit",
    "themes_registry": legacy_theme_registry,
    "news_registry": legacy_news_registry,
    "notice_context": notice_ctx.to_dict(),    # #18
    "theme_context":  theme_ctx.to_dict(),     # #19 신규
}
decision = ThemeInsightAgent().decide({"symbol": "LUNA"}, ctx)
```

후속 단계에서 NewsTrendAgent 가 도입되면 같은 `ThemeContextBuilder.build_theme_context`
결과를 그대로 입력으로 사용한다.

## 9. REST API

### 9.1 `GET /api/theme-signals`

쿼리:
- `source`, `provider`, `theme`, `symbol`, `since_hours`, `limit` (max 1000)

응답:
```jsonc
{
  "signals": [{ "source": "news", "provider": "mock_news", ... }],
  "summary": {
    "by_source": {...},
    "by_theme":  {...},
    "by_risk_flag": {...},
    "updated_at": "..."
  },
  "used_for_order": false,
  "direct_order_allowed": false
}
```

### 9.2 `POST /api/theme-signals/collect` (admin)

```jsonc
// Request
{ "provider": "mock", "since_hours": 72 }

// Response
{
  "fetched": 9, "inserted": 9, "updated": 0, "skipped": 0,
  "by_source": {"trend": 1, "news": 3, "..."},
  "by_theme":  {"ETF": 1, "..."},
  "by_risk_flag": {"regulatory_attention": 1, "..."},
  "used_for_order": false,
  "direct_order_allowed": false
}
```

`X-Admin-Token` 헤더 필요. 기본 provider 는 `mock`. 실제 외부 API 호출 없음.

### 9.3 `GET /api/theme-signals/context`

쿼리:
- `symbols=BTC,LUNA` (콤마, 선택)
- `themes_csv=ETF,Delisting` (선택)
- `sources_csv=news,disclosure` (선택)
- `lookback_hours=72`

`ThemeContextBuilder.to_dict()` 결과를 그대로 반환. `direct_order_allowed`/
`used_for_order` 항상 `false`.

### 9.4 `GET /api/theme-signals/sources`

```jsonc
{
  "sources":    ["trend", "news", "disclosure", "theme", "macro_fx", "other"],
  "risk_flags": ["high_news_attention", "regulatory_attention", "..."],
  "used_for_order": false,
  "direct_order_allowed": false
}
```

### 9.5 `POST /api/theme-signals/filter`

Watchlist 후보 리스트에 theme context 와 review flag 를 부여한다. 본 엔드포인트의
응답은 `candidate_filter_ok` / `candidate_filter_review_required` 만 사용하며 BUY/SELL
은 절대 반환하지 않는다.

```jsonc
// Request
{
  "candidates": [
    {"symbol": "LUNA", "exchange": "upbit"},
    {"symbol": "BTC",  "exchange": "upbit"}
  ],
  "lookback_hours": 72
}

// Response
{
  "candidates": [
    {
      "symbol": "LUNA",
      "exchange": "upbit",
      "themes": ["Delisting"],
      "risk_flags": ["delisting_related_theme"],
      "recommendation": "candidate_filter_review_required",
      "risk_notes": ["..."],
      "used_for_order": false,
      "direct_order_allowed": false
    }
  ],
  "used_for_order": false,
  "direct_order_allowed": false
}
```

## 10. 안전 / 정책

- Theme/News/Trend 데이터는 **후보 필터와 리스크 설명** 용도. 직접 매매 신호가 아니다.
- BUY/SELL/ENTER/EXIT/LONG/SHORT 토큰은 응답 페이로드 어디에도 등장하지 않는다 —
  `_assert_no_action_tokens` 가드 + 화이트리스트 강제 + 회귀 테스트 4개로 보장.
- `used_for_order` / `direct_order_allowed` 는 DB 컬럼/API 응답/Agent context 어디서나
  영구 `False`.
- `app.market.theme_signals` / `app.market.theme_context` 는 `app.brokers.*` /
  `app.execution.*` 를 import 하지 않는다 (회귀 테스트로 강제).
- 새 위험 기능 플래그 도입 없음 — 본 계층 자체가 실거래에 영향 주지 않음.
- API Key/Secret/Token 저장 컬럼 없음 (CLAUDE.md §2.1).

## 11. 후속 단계와의 관계

- #20 Exchange Adapter Interface 는 본 작업 범위가 아니다.
- 실제 Google Trends / 뉴스 / 공시 adapter 는 후속 PR에서 `ThemeProvider` Protocol 만
  만족시키며 추가한다.
- NewsTrendAgent 가 도입되면 같은 `ThemeContextBuilder` 결과를 input 으로 사용 가능.
- 본 단계 완료는 실거래 허가가 아니다 (CLAUDE.md §2.6 승격 절차 별도).

## 12. 회귀 테스트

`backend/tests/test_theme_signals.py` — 44 케이스. 분류기 / risk_flag 추론 /
정규화 / content_hash / signal_id dedup / content_hash dedup / ThemeFilter /
ThemeContextBuilder / REST API (admin gating / collect / list / context / sources /
filter) / ORM UNIQUE 제약 / broker·execution 미참조 정적 검증 / 금지 문자열 부재 /
action 토큰 부재 / SOURCES·ALLOWED_RISK_FLAGS 카탈로그.

```
cd backend
python -m pytest tests/test_theme_signals.py -q
```
