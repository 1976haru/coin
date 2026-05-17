# Watchlist / Universe — 체크리스트 #14

이 문서는 Watchlist 의 **역할 / 한계 / 운용 원칙** 을 정리한다.
구현은 `backend/app/market/watchlist.py`, REST 는 `backend/app/api/watchlist.py`,
DB 모델은 `backend/app/db/models.py:WatchlistEntry`.

---

## 1. 무엇인가 — 그리고 무엇이 *아닌가*

Watchlist 는 **후보 universe 를 제한하는 안전장치** 다.

| Watchlist 는… | … 가 아니다 |
|---|---|
| 분석·수집·전략이 다룰 **후보 셋** 을 좁힌다 | 주문 허용 목록(allow-list)이 아니다 |
| 거래소·심볼·tag 메타데이터를 저장한다 | RiskManager/OrderGuard/PermissionGate 를 우회시키지 않는다 |
| 운영자가 명시적으로 등록해야 한다 | 시장 전체를 자동 스캔하지 않는다 |

> **여기에 등록되어 있다고 해서 자동으로 주문되는 것이 아니다.**
> 신호 → 단일 주문 경로 (CLAUDE.md §2.4) 의 모든 게이트를 그대로 통과해야 한다.

---

## 2. 왜 universe 를 제한하는가

1. **데이터 폭발 방지** — 거래소가 제공하는 수백~수천 심볼을 모두 처리하면
   freshness/rate-limit/저장소 모두 무너진다.
2. **테스트 가능성** — PAPER/MOCK 단계에서 5~30 종목 수준으로 좁혀야 결정론적
   회귀가 가능하다.
3. **운영자 책임 명시화** — 어떤 심볼을 다루고 있는지 명문화 (config/watchlists/*.json).
4. **사고 표면 축소** — 의도하지 않은 심볼(예: 상폐 코인, 저유동성 코인)이
   자동 진입 경로에 들어오지 못한다.

**초기 universe 크기 가이드라인: 20 ~ 100 종목.**

---

## 3. 모델 / 키 구조

`WatchlistEntry` (table: `watchlist`)

| 컬럼 | 의미 |
|---|---|
| `list_name` | 그룹 (예: `default`, `majors`, `kimp_pairs`) |
| `symbol` | 거래소-native 심볼. 거래소마다 형식이 다르다 (예: upbit `KRW-BTC` vs binance `BTC/USDT`). 본 모듈은 형식을 강하게 검사하지 않고 **저장 전 strip + upper** 만 적용한다. |
| `exchange` | `upbit / binance / okx / mock / paper` 화이트리스트만 허용. strip + lower. |
| `enabled` | false 면 조회는 되지만 Strategy/Collector 가 건너뛴다 |
| `max_notional_usdt_override` | 글로벌 `MAX_ORDER_NOTIONAL_USDT` 를 심볼별 더 **엄격하게만** 덮어쓴다. 확장은 RiskManager 가 거부한다. |
| `tags` / `note` | 운영 라벨 |

UNIQUE 제약: `(list_name, symbol, exchange)` — 같은 조합 중복 등록 차단.

---

## 4. CoinSymbol(#13) 과의 역할 분리

| 모델 | 역할 |
|---|---|
| `CoinSymbol` (#13) | 거래소-심볼 **마스터 / 메타데이터** — tick_size, lot_size, 상태 등. 수집 가능한 심볼의 기준 데이터. |
| `WatchlistEntry` (#14) | 운영자가 **분석·수집·전략 후보로 등록한 셋**. 운영 의사결정. |

두 테이블은 별개다. 합치지 않는다.
- `CoinSymbol` 에 있어도 Watchlist 에 없으면 Strategy/Collector 가 다루지 않는다.
- `Watchlist` 에 있어도 RiskManager / OrderGuard / PermissionGate / ApprovalQueue 단일 경로의 모든 게이트를 통과해야 한다.

회귀 테스트: `tests/test_watchlist.py::test_coin_symbol_and_watchlist_entry_are_distinct`.

---

## 5. universe 크기 제한

### list_name 별 cap (기본값)

| list_name | enabled cap |
|---|---:|
| `default` | 50 |
| `majors` | 20 |
| `kimp_pairs` | 100 |
| (그 외) | 50 |

### 전체 cap

환경변수 `WATCHLIST_MAX_ENABLED_TOTAL` (기본 100).

### 동작

- `POST /api/watchlist` 또는 `PATCH /api/watchlist/{id}/enable` 시
  - 해당 list_name 의 enabled 항목 + 1 > list_cap → **409**
  - 전체 enabled 합 + 1 > total_cap → **409**
- `enabled=false` 항목은 **cap 계산에서 제외** (보류 의도가 있는 항목 보존).

---

## 6. 정규화 / 검증

| 입력 | 정규화 | 거부 사유 |
|---|---|---|
| `symbol` | `strip + upper` | 빈/공백/whitespace 포함/32자 초과 |
| `exchange` | `strip + lower` | 화이트리스트(`upbit/binance/okx/mock/paper`) 외 |
| `list_name` | `strip + lower` | 빈/공백 포함/32자 초과 |

심볼 *포맷* 자체는 강하게 제한하지 않는다 — 거래소별로 표기가 다르기 때문(예: `BTC`, `KRW-BTC`, `BTC/USDT`, `BTC-USDT`).

API 매핑:
- `WatchlistValidationError` → **400**
- `WatchlistLimitError` → **409**
- `WatchlistDuplicateError` → **409**
- `WatchlistNotFoundError` → **404**

---

## 7. Seed 템플릿

PAPER/MOCK 단계에서 빠르게 시작할 수 있도록 JSON 템플릿을 제공한다.

```
config/watchlists/default.json     # PAPER 테스트용 7종
config/watchlists/majors.json      # PAPER 테스트용 5종 (majors)
config/watchlists/kimp_pairs.json  # PAPER 테스트용 김프 페어 6종
```

> **모두 PAPER/MOCK 테스트용 예시 — 실제 투자 추천이 아니다.**

import (멱등):

```bash
cd backend
python -m app.market.watchlist_seed ../config/watchlists/default.json
# 결과 JSON: {added, skipped_duplicate, skipped_invalid, skipped_limit, updated}
```

중복 항목은 기본 건너뛴다. `--update-tags` 옵션으로 기존 행의 tags/note 만 덮어쓸 수 있다.

---

## 8. REST API

```
GET    /api/watchlist                       # 공개. entries + lists + summary 반환
POST   /api/watchlist                       # admin. body: {symbol, exchange?, list_name?, enabled?, tags?, note?}
DELETE /api/watchlist/{id}                  # admin
PATCH  /api/watchlist/{id}/enable           # admin (cap 검증)
PATCH  /api/watchlist/{id}/disable          # admin
```

GET 응답 예시:

```json
{
  "entries": [ ... ],
  "lists":   ["default", "majors"],
  "summary": {
    "total": 30, "enabled": 25, "disabled": 5,
    "by_exchange":  {"upbit": 10, "binance": 15},
    "by_list_name": {"default": 20, "majors": 5},
    "limits": {
      "default": 50, "majors": 20, "kimp_pairs": 100,
      "other": 50, "max_enabled_total": 100
    }
  }
}
```

쓰기 동작은 모두 `X-Admin-Token` 헤더 필요. token 미동봉/오답 시 **401**.

---

## 9. 안전 원칙 — 변경되지 않은 것

본 작업은 **운영자가 정한 universe 를 명시화 / 제한 / 정규화** 하는 범위에 한정된다.

- 실제 거래소 LIVE 주문 기능 추가 없음
- `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` / `ENABLE_CRYPTO_FUTURES_LIVE` 기본값 변경 없음
- frontend 에 API key / secret / token 저장 없음
- Upbit / OKX / Binance 실거래 주문 연동 확장 없음
- 전체 시장 자동 스캔 기능 없음
- 15번 Market Data Collector 작업 미진행 — 본 작업의 범위가 아니다

**Watchlist 14번 완료 = universe 안전장치 + seed 템플릿 + summary API 의 완료.
LIVE 실거래 허가가 아니다.**

---

## 10. 향후 확장 메모 (별도 체크리스트)

| 영역 | 메모 |
|---|---|
| Universe 자동 정리 | 상폐/유의종목 자동 disable (NoticeRegistry 통합) — #18 후속 |
| 동적 limit | 운영 상태에 따라 cap 자동 조절 — 별도 PR |
| 거래소별 native symbol 어댑터 | Watchlist 의 `symbol` 을 거래소 native 로 정확 변환 — #21~#23 어댑터와 통합 |
| 멱등 일괄 sync | seed JSON 한 파일과 DB 의 list_name 한 그룹을 정확히 동기화 (없는 행 disable) — 별도 PR |
