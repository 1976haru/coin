# Crypto Database Schema — 체크리스트 #13

코인 전용 DB 스키마. SQLAlchemy ORM은 `backend/app/db/models.py`,
Alembic 마이그레이션은 `backend/app/db/migrations/versions/0003_crypto_schema.py` 에 있다.

---

## 1. 목적

체크리스트 #13의 코인 데이터 영역. 다음 9개 테이블을 추가한다.

| 테이블 | 역할 |
|---|---|
| `coin_symbol` | 거래소-심볼 마스터 (tick/lot/상태) |
| `coin_candle` | OHLCV 봉 |
| `coin_tick` | 체결 틱 (시계열) |
| `coin_orderbook_snapshot` | 호가창 스냅샷 |
| `coin_signal` | 전략 advisory 신호 (주문 아님) |
| `coin_order` | paper/mock/research 주문 추적 |
| `coin_trade` | 체결 fill 레코드 |
| `coin_position` | 코인 포지션 (mode별) |
| `coin_risk_event` | 리스크/가드 이벤트 |

본 작업의 범위는 **스키마 추가**까지. 거래소 API 호출, 주문 실행, 정산 로직은
포함하지 않는다 (CLAUDE.md §2.1 ~ §2.5).

---

## 2. 기존 주식/공통 테이블과 분리 이유 — `coin_` prefix

- 기존 `orders`, `positions`, `audit_events`, `agent_decisions`, `watchlist` 는
  주식·공통용 lifecycle 모델. 컬럼 스키마(통화, 수량 정밀도, 모드 정의)가 코인과
  다르므로 **재사용하지 않고 분리**한다.
- 같은 메타정보를 두 영역에서 동시에 다루는 혼란을 피하기 위해 모든 코인 테이블에
  `coin_` prefix를 부여한다.
- 향후 KIS/주식 영역과 코인 영역을 별도 마이그레이션 라인으로 운영하기 쉽도록 한다.

---

## 3. 가격/수량은 `Numeric(28, 12)` 사용

- BTC sub-satoshi, SHIB·PEPE 같은 저단가/대수량, USDT 가격까지 모두 한 컬럼 정의로
  표현해야 한다.
- Python `float`는 0.1 + 0.2 ≠ 0.3 류 오차가 누적된다. PnL/체결 합산 시 위험.
- 따라서 가격·수량은 `Numeric(28, 12)` (정수부 16자리 + 소수부 12자리)로 통일.
  `confidence` 같은 통계 스칼라는 그대로 `Float` 유지.

---

## 4. CoinSignal은 advisory — 주문이 아니다

`coin_signal.used_for_order` 기본값은 **False**.

- AI Agent / Strategy가 만든 결과는 **분석·추천**으로만 기록된다 (CLAUDE.md §2.3).
- 실제 주문 생성은 OrderGateway → PermissionGate → ApprovalQueue 단일 경로를 거친다.
- 주문이 만들어지고 그 트리거가 특정 신호였다면 그때서야 신호 레코드에
  `used_for_order=True` / `coin_order.signal_id` 로 역참조를 남긴다.
- 본 마이그레이션은 broker / OrderExecutor / route_order 와 무관하다. 그쪽 코드는
  수정하지 않는다.

---

## 5. CoinOrder는 paper/mock 추적용

`coin_order.mode` 기본값은 **`PAPER`**. LIVE가 아니다.

허용되는 값(컨벤션, DB 레벨 CHECK는 두지 않음):

- `PAPER`   : 가상 매매. 실거래 호출 없음.
- `MOCK`    : 고정 시나리오에서 동작 검증.
- `SHADOW`  : 실시간 데이터를 받아도 주문은 송신하지 않음. 비교용.
- `RESEARCH`: 백테스트/리서치 기록.

`LIVE` 모드 전환은 CLAUDE.md §2.6 승격 절차(별도 환경변수 + 별도 문서 + 별도 테스트)
모두 통과 후에만 가능. 본 스키마는 LIVE를 받아들일 수 있는 컬럼 형태이긴 하나
**기본값과 코드 경로 모두 LIVE를 가정하지 않는다.**

---

## 6. AgentMemory는 기존 테이블 재사용

코인 데이터와 AgentMemory(또는 다른 도메인 레코드)를 연결할 필요가 생기면, 신규 테이블을
만들지 말고 **`source_kind` / `source_id` / `tags` / `meta` 공통 컬럼**으로 느슨하게
연결한다.

| 컬럼 | 의미 |
|---|---|
| `source_kind` | `"strategy" / "agent_memory" / "manual" / "backtest" / "shadow"` 등 도메인 키 |
| `source_id`   | 해당 도메인의 ID(문자열). FK가 아닌 약한 참조. |
| `tags`        | JSON list. 빠른 필터링용 라벨. |
| `meta`        | JSON dict. 도메인-종속 부속 정보. 단, secret/PII 금지. |

해당 컬럼은 `coin_signal`, `coin_order`, `coin_trade`(meta만), `coin_risk_event` 에
공통적으로 적용된다.

---

## 7. 실거래 API Key / Secret 저장 금지

CLAUDE.md §2.1 의 절대 원칙:

- API Key, API Secret, Passphrase, Access Token, 계좌번호 등은 **DB 컬럼으로 만들지 않는다.**
- `coin_*` 테이블 어떤 컬럼도 secret을 보관할 수 있는 자리가 아니다.
- `meta` JSON에도 redaction을 거치지 않은 원본 secret을 넣지 않는다.
- 만약 향후 거래소 adapter를 만든다면 secret은 OS env / OS keychain / 외부 secret store
  중 어디로 둘지 별도 문서에서 결정한다 (본 #13 작업 범위가 아님).

회귀 방지를 위해 `tests/test_db_crypto_schema.py::test_no_secret_columns_in_coin_models`
가 coin_* 테이블 컬럼명에 secret 의심 이름이 없는지 검사한다.

---

## 8. 향후 확장 메모 (별도 체크리스트에서 처리)

| 영역 | 메모 |
|---|---|
| Exchange adapter   | Upbit/Binance/OKX 등 read-only collector. Secret 저장 없이 환경변수 기반. |
| Kimp strategy      | 김프/역김프 전략. `ENABLE_KIMP_STRATEGY` 기본 false 유지. |
| Paper fill engine  | `coin_order` → `coin_trade` 변환을 결정론적으로 시뮬레이션. 슬리피지 모델 포함. |
| Orderbook slippage | `coin_orderbook_snapshot` 으로부터 시장 충격 모델 산출. |
| Report / scoreboard| `coin_trade` + `coin_position` 기반 PnL/리스크 리포트. |

본 문서는 #13 스코프만 다룬다. 위 항목들은 각자의 체크리스트에서 별도 PR로 들어온다.

---

## 9. 마이그레이션 운영

```
cd backend
alembic upgrade head      # 0001 → 0002 → 0003 적용
alembic downgrade 0002    # coin_* 9개 테이블만 롤백
```

`alembic_version` 테이블의 마지막 리비전이 `0003` 이면 본 스키마가 적용된 것이다.
