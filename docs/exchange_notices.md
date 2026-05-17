# Exchange Notices (체크리스트 #18)

> Agent Trader Crypto OS v1 — 거래소 구조적 리스크 context 수집 계층

## 1. 목적과 범위

Exchange Notices 는 **거래소 공지 이벤트**(입출금 중단, 유의종목 지정, 상장폐지/거래지원
종료, 신규 상장, 시스템 점검, 거래 일시 정지, 정책 변경)를 수집·정규화·중복 제거·DB
저장하고, Agent / 후보 필터 / 사람이 사용할 수 있는 **read-only context 계층**이다.

본 계층은 다음만 한다.

- 거래소 공지 텍스트 수집 (mock source 또는 read-only 외부 source)
- 정규화 + notice_type 분류 + severity 산출
- 중복 제거 후 `exchange_notice` 테이블에 영속화
- Agent / 후보 필터 / UI 가 읽을 수 있는 요약 context 생성

본 계층은 다음을 **하지 않는다**.

- 공지 이벤트를 직접 매수/매도 트리거로 사용 ❌
- 거래소 LIVE 주문 / private endpoint 호출 ❌
- 출금 권한 키 사용 ❌
- 전체 시장 자동 스캔 ❌
- AI Agent 가 본 데이터를 근거로 직접 주문 ❌

`direct_order_allowed` 필드는 영구 `False` — 응답에 항상 포함되어 호출자가 본 데이터를
주문 권한으로 오인하지 않도록 한다 (CLAUDE.md §2.3).

## 2. 데이터 모델

### 2.1 `exchange_notice` 테이블 (`ExchangeNotice` ORM)

| 컬럼 | 타입 | 비고 |
|---|---|---|
| `id` | int PK | |
| `exchange` | string(32) | lower-case 정규화 |
| `notice_id` | string(128) nullable | 거래소측 식별자 (있을 때만) |
| `title` | text | strip + 최대 512자 |
| `url` | text | optional |
| `category` | string(64) | source 가 준 카테고리 원본 |
| `notice_type` | string(48) | 8개 분류 (아래 §3) |
| `severity` | string(16) | INFO / WARNING / HIGH / CRITICAL |
| `body` | text | 공지 본문 (secret 없음) |
| `symbols` | JSON list[str] | upper-case, 정렬, dedup |
| `published_at` | datetime tz nullable | 거래소가 제공한 게시 시각 |
| `collected_at` | datetime tz | 수집기가 본 시각 |
| `content_hash` | sha256(hex) | exchange/title/body 기반 |
| `source_name` | string(64) | mock / upbit_rss / ... |
| `direct_order_allowed` | bool | **영구 False** |
| `note` | text | |
| `raw_payload` | JSON | 원본 dict (secret 미포함) |
| `updated_at` | datetime tz | onupdate |

#### Unique 제약 (중복 제거 키)
1. `(exchange, notice_id)` — `notice_id` 가 있을 때 우선 매칭.
2. `(exchange, content_hash)` — `notice_id` 가 없을 때 본문 기반 매칭.

같은 공지가 두 번 수집되어도 row 가 중복되지 않으며, 새 본문은 기존 row 를 update 한다.

### 2.2 Alembic 마이그레이션

`backend/app/db/migrations/versions/0004_exchange_notices.py` — `0003_crypto_schema` 다음
revision. downgrade 안전.

## 3. notice_type / severity 분류

### 3.1 notice_type (8개)

| notice_type | 한국어 키워드 | 영문 키워드 | 기본 severity |
|---|---|---|---|
| `DEPOSIT_WITHDRAWAL_SUSPENSION` | 입출금 중단, 지갑 점검 | deposit/withdrawal, wallet maintenance | HIGH |
| `CAUTION` | 유의종목, 투자유의 | caution, warning, monitoring | WARNING |
| `DELISTING` | 상장폐지, 거래지원 종료 | delisting, delist | CRITICAL |
| `LISTING` | 신규 상장, 거래지원 개시 | new listing | INFO |
| `MAINTENANCE` | 시스템 점검 | maintenance | WARNING |
| `TRADING_SUSPENSION` | 거래 중단, 거래 일시 정지 | trading suspension/halt | CRITICAL |
| `POLICY` | 수수료, 약관, 정책 변경 | fee schedule, policy update | INFO |
| `OTHER` | (분류 불가) | (fallback) | INFO |

키워드는 제목/본문/카테고리에 대소문자 무시로 부분 매칭한다. 우선순위 순서로 평가하며
처음 매칭된 type 을 사용한다.

### 3.2 severity 상향 규칙

- 본문/제목에 `긴급`/`urgent`/`emergency`/`즉시` 류 키워드 → 한 단계 상향
- 본문/제목에 `상장폐지`/`거래 중단`/`delisting`/`trading suspension`/`trading halt` →
  `CRITICAL` 강제

## 4. NoticeSource / MockNoticeSource

### 4.1 Protocol

```python
class NoticeSource(Protocol):
    name: str

    def fetch_notices(
        self,
        exchange: str,
        since: datetime | None = None,
    ) -> list[RawNotice]: ...
```

`RawNotice` 는 정규화 전 입력 — `title` 만 필수. 나머지(`notice_id`/`url`/`category`/
`published_at`/`body`/`symbols`/`raw_payload`) 는 모두 optional.

### 4.2 `MockNoticeSource`

외부 네트워크 호출 없는 결정론적 source. 8개 notice_type 을 최소 1개씩 포함하며
일부는 `notice_id` 없이 `content_hash` 기반 dedup 검증용으로 설계되어 있다.

```python
from app.market.notice_collector import MockNoticeSource, NoticeCollector

collector = NoticeCollector({"mock": MockNoticeSource("mock")})
```

### 4.3 실제 거래소 adapter

본 단계 범위 밖 — 후속 PR에서 read-only Upbit/OKX/Binance RSS·HTML adapter 를 같은
Protocol 로 구현해 collector 에 주입한다. 본 collector 는 어떤 거래소 SDK / private
endpoint / 주문 endpoint 도 import 하지 않는다.

## 5. NoticeCollector

```python
from app.db import session_scope
from app.market.notice_collector import NoticeCollector, MockNoticeSource

c = NoticeCollector({"mock": MockNoticeSource("mock")})
with session_scope() as s:
    r = c.collect_once(s, exchange="mock", source_name="mock")
print(r.fetched, r.inserted, r.updated, r.skipped, r.by_type, r.by_severity)
```

upsert 규칙: `(exchange, notice_id)` 우선 → 없으면 `(exchange, content_hash)`.
`direct_order_allowed` 는 collector 가 항상 `False` 로 기록 — 영구 (CLAUDE.md §2.3).

## 6. NoticeContextBuilder (Agent context)

```python
from app.market.notice_context import NoticeContextBuilder

with session_scope() as s:
    ctx = NoticeContextBuilder(s).build_notice_context(
        symbols=["BTC", "XRP", "LUNA"],
        lookback_hours=72,
    )
print(ctx.to_dict())
```

응답 구조 요약:

```jsonc
{
  "generated_at": "...",
  "lookback_hours": 72,
  "total_notices": 7,
  "by_type": {"DELISTING": 1, "DEPOSIT_WITHDRAWAL_SUSPENSION": 1, "...": 0},
  "by_severity": {"CRITICAL": 2, "HIGH": 1, "WARNING": 2, "INFO": 2},
  "high_risk_symbols": ["LUNA", "SOL"],
  "symbol_summaries": [
    {
      "symbol": "LUNA",
      "risk_flags": ["delisting_or_termination"],
      "severity": "CRITICAL",
      "high_risk_count": 1,
      "notice_count": 1,
      "recommendation": "candidate_filter_review_required",
      "direct_order_allowed": false
    }
  ],
  "recent_titles": ["..."],
  "human_summary": "최근 72시간 내 공지 7건 ... 직접 주문 트리거가 아닙니다.",
  "candidate_filter_flags": ["delisting_or_termination", "..."],
  "risk_notes": ["[CRITICAL] DELISTING (mock): ..."],
  "direct_order_allowed": false
}
```

**핵심 — 본 context 는 후보 필터/리스크 설명 용도다.** 직접 주문 지시·매수 신호·매도
신호를 만들지 않으며 `direct_order_allowed` 는 항상 `false`.

### 6.1 candidate filter flag 매핑

| notice_type | flag |
|---|---|
| DEPOSIT_WITHDRAWAL_SUSPENSION | `deposit_withdrawal_suspended` |
| CAUTION | `caution_notice` |
| DELISTING | `delisting_or_termination` |
| TRADING_SUSPENSION | `trading_suspended` |
| MAINTENANCE | `maintenance_in_progress` |
| LISTING | `new_listing` |
| POLICY | `policy_change` |
| OTHER | `other_notice` |

### 6.2 `recommendation` 산출

| 조건 | recommendation |
|---|---|
| HIGH/CRITICAL 1건 이상 또는 입출금 중단/상장폐지/거래 중단 플래그 | `candidate_filter_review_required` |
| 그 외 | `candidate_filter_ok` |

## 7. Agent 연결

`ThemeInsightAgent.decide()` 는 기존 거래 결정을 변경하지 않고 `ctx["notice_context"]`
(NoticeContextBuilder 결과 dict) 가 있을 때만 `explain_text` 에 부가 정보로 첨부한다.

```python
ctx = {
    "symbol": "LUNA",
    "exchange": "mock",
    "notices_registry": legacy_registry,         # 기존 in-memory 사용 유지
    "notice_context": notice_context.to_dict(),  # 신규 — DB-backed 영속 데이터
}
decision = ThemeInsightAgent().decide({"symbol": "LUNA"}, ctx)
```

NewsTrendAgent 가 별도로 존재하지 않으므로 본 단계에서는 새 대형 agent 를 만들지
않았다. 후속 단계 (#19) Trend/News/Theme Signals 작업에서 NewsTrendAgent 가 추가되면
같은 `NoticeContextBuilder` 결과를 그대로 입력으로 사용할 수 있다.

## 8. REST API

### 8.1 `GET /api/notices`

기존 legacy 응답을 유지하며 영속 레이어 데이터를 추가로 노출한다.

쿼리:
- `active_only=true` (기본) — 메모리 레지스트리 active 필터
- `exchange`, `symbol`, `notice_type`, `severity`, `since_hours`

응답 (요약):
```jsonc
{
  "notices": [...],            // legacy 메모리 항목
  "count": 1,                  // legacy
  "exchange_notices": [...],   // DB-backed ExchangeNotice
  "summary": {
    "by_type": {...},
    "by_severity": {...},
    "high_risk_symbols": [...],
    "updated_at": "..."
  },
  "direct_order_allowed": false
}
```

### 8.2 `POST /api/notices/collect` (admin)

```jsonc
// Request
{ "exchange": "mock", "source": "mock", "since_hours": 72 }

// Response
{
  "fetched":  8,
  "inserted": 8,
  "updated":  0,
  "skipped":  0,
  "by_type":  {"DELISTING": 1, "...": 0},
  "by_severity": {"CRITICAL": 2, "...": 0},
  "direct_order_allowed": false
}
```

`X-Admin-Token` 헤더 필요. 기본 source 는 `mock`. 실제 거래소 사이트 호출 없음.

### 8.3 `GET /api/notices/context`

쿼리:
- `symbols=LUNA,XRP` (콤마 구분, 선택)
- `lookback_hours=72` (기본 72)
- `exchange=mock` (선택)

`NoticeContextBuilder.to_dict()` 결과를 그대로 반환. `direct_order_allowed` 항상
`false`.

### 8.4 `GET /api/notices/types`

```jsonc
{
  "notice_types": ["DEPOSIT_WITHDRAWAL_SUSPENSION", "CAUTION", "..."],
  "severities":   ["INFO", "WARNING", "HIGH", "CRITICAL"],
  "direct_order_allowed": false
}
```

### 8.5 기존 엔드포인트 (변경 없음)

- `GET /api/notices/symbol/{exchange}/{symbol}` — KimpStrategy 호환 메모리 레지스트리
- `POST /api/notices` / `DELETE /api/notices/{id}` (admin) — 메모리 레지스트리 CRUD

## 9. 안전 / 정책

- 공지 이벤트는 **후보 필터와 리스크 설명** 용도. 직접 주문 트리거가 아니다.
- `direct_order_allowed` 는 DB 컬럼/API 응답/Agent context 어디서나 영구 `False`.
- `app.market.notice_collector` / `app.market.notice_context` 는 `app.brokers.*` /
  `app.execution.*` 를 import 하지 않는다 (회귀 테스트로 강제).
- 새 위험 기능 플래그 도입 없음 — 본 계층 자체가 실거래에 영향 주지 않음.
- API Key/Secret/Token 저장 컬럼 없음 (CLAUDE.md §2.1).

## 10. 후속 단계와의 관계

- #19 Trend/News/Theme Signals 는 본 작업 범위가 아니다. NewsTrendAgent 가 도입되면
  같은 `NoticeContextBuilder` 결과를 input 으로 사용 가능.
- 실제 거래소 notice adapter (Upbit RSS, OKX announcement, Binance support) 는
  후속 단계에서 `NoticeSource` Protocol 만 만족시키며 추가한다.
- 본 단계 완료는 실거래 허가가 아니다 (CLAUDE.md §2.6 승격 절차 별도).

## 11. 회귀 테스트

`backend/tests/test_exchange_notices.py` — 55 케이스. 분류기 / severity / 정규화 /
content_hash / notice_id dedup / content_hash dedup / NoticeContextBuilder /
REST API (admin gating / collect / context / types) / ORM UNIQUE 제약 /
broker·execution 미참조 정적 검증 / 금지 문자열 부재 / NOTICE_TYPES 카탈로그 크기.

```
cd backend
python -m pytest tests/test_exchange_notices.py -q
```
