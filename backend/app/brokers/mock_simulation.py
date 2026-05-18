"""MockBroker — 체크리스트 #24.

실제 거래소 없이 주문 → 체결 → 잔고 → 포지션 반영을 결정론적으로 재현하는 시뮬레이션
브로커. CI / PAPER / MOCK 테스트 전용 안전 실행 환경이다.

원칙 (CLAUDE.md §2.2 / §2.3 / §2.4):
  - 외부 네트워크 호출 절대 없음 (정적/동적 회귀로 강제).
  - LIVE mode 주문은 무조건 거부 — mode 가 항상 ``MOCK`` 또는 ``PAPER``.
  - Strategy/Agent 가 직접 호출 금지 (모듈 경계 + 정적 회귀).
  - 모든 결과에 ``mode``, ``is_real_trade=False``, ``execution_source="mock_broker"``,
    ``warning="Mock execution only. Not real profit or real trade."`` 포함.
  - secret / api_key / token 류 키는 응답에 들어가지 않는다 (audit sanitize).

본 모듈과 ``MockExchangeAdapter``의 차이:
  - ``MockExchangeAdapter`` (``mock_broker.py``) — ExchangeAdapter contract 준수.
    얇은 paper-only 어댑터 (단일 잔고, MARKET 즉시 FILLED).
  - ``MockBroker`` (본 파일) — 시뮬레이션 *브로커*. 다중 자산 잔고, locked balance,
    position book (avg_entry_price + realized/unrealized PnL), LIMIT 주문 book,
    fee/slippage, partial fill 등 전체 주문 라이프사이클.

OrderGateway 호환:
  - ``place_order(dict) -> dict`` / ``cancel_order(order_id) -> dict`` 시그니처가
    기존 ``PaperBroker`` 와 호환되어 drop-in 사용 가능.
"""
from __future__ import annotations
import hashlib
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


_FORBIDDEN_RESPONSE_KEYS: tuple[str, ...] = (
    "api_key", "api_secret", "secret", "access_token", "token",
    "passphrase", "password", "private_key",
    "ok_access_key", "ok_access_sign", "ok_access_passphrase",
    "x_mbx_apikey",
)

_MOCK_WARNING: str = "Mock execution only. Not real profit or real trade."


# ── Config ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class MockBrokerConfig:
    """MockBroker 동작 파라미터.

    fee_bps / slippage_bps 는 basis point 단위 (1bp = 0.01%).
    """

    base_currency: str = "USDT"                 # quote 통화 ( cash )
    fee_bps: float = 5.0                        # 5 bps = 0.05%
    slippage_bps: float = 0.0                   # 기본 0 — 결정론
    allow_short: bool = False
    allow_margin: bool = False
    partial_fill_enabled: bool = False
    deterministic_seed: int = 0
    max_order_notional: float = 0.0             # 0 = 무제한 (브로커 단계, OrderGuard 가 별도 가드)
    mode: str = "MOCK"                          # MOCK | PAPER (LIVE 금지)
    initial_balances: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.mode not in ("MOCK", "PAPER"):
            raise ValueError(
                f"MockBroker.mode must be MOCK or PAPER, got {self.mode!r}"
            )
        if self.fee_bps < 0 or self.slippage_bps < 0:
            raise ValueError("fee_bps / slippage_bps must be >= 0")
        if self.max_order_notional < 0:
            raise ValueError("max_order_notional must be >= 0")


# ── 잔고 / 포지션 ─────────────────────────────────────────────────


class MockAccountState:
    """다중 자산 잔고. 자산별 free / locked 분리.

    locked balance 는 open LIMIT 주문 미체결 시 보존된다 (cancel 시 해제).
    """

    def __init__(self, initial_balances: dict | None = None):
        # ccy(str) → {"free": float, "locked": float}
        self._book: dict[str, dict[str, float]] = {}
        for k, v in (initial_balances or {}).items():
            self.deposit(k, float(v))

    def deposit(self, ccy: str, amount: float) -> None:
        if amount < 0:
            raise ValueError("deposit amount must be >= 0")
        c = ccy.upper()
        slot = self._book.setdefault(c, {"free": 0.0, "locked": 0.0})
        slot["free"] = round(slot["free"] + float(amount), 12)

    def free(self, ccy: str) -> float:
        return self._book.get(ccy.upper(), {"free": 0.0, "locked": 0.0})["free"]

    def locked(self, ccy: str) -> float:
        return self._book.get(ccy.upper(), {"free": 0.0, "locked": 0.0})["locked"]

    def total(self, ccy: str) -> float:
        return self.free(ccy) + self.locked(ccy)

    def lock(self, ccy: str, amount: float) -> None:
        if amount < 0:
            raise ValueError("lock amount must be >= 0")
        slot = self._book.setdefault(ccy.upper(), {"free": 0.0, "locked": 0.0})
        if slot["free"] + 1e-12 < amount:
            raise ValueError(
                f"insufficient_balance to lock {amount} {ccy} (free={slot['free']})"
            )
        slot["free"] = round(slot["free"] - amount, 12)
        slot["locked"] = round(slot["locked"] + amount, 12)

    def unlock(self, ccy: str, amount: float) -> None:
        if amount < 0:
            raise ValueError("unlock amount must be >= 0")
        slot = self._book.setdefault(ccy.upper(), {"free": 0.0, "locked": 0.0})
        avail = min(slot["locked"], amount)
        slot["locked"] = round(slot["locked"] - avail, 12)
        slot["free"] = round(slot["free"] + avail, 12)

    def settle_buy(self, base_ccy: str, base_amt: float,
                   quote_ccy: str, quote_used: float,
                   *, from_locked: bool = False) -> None:
        """체결 후 정산: locked quote → 차감, base → 증가."""
        slot_q = self._book.setdefault(quote_ccy.upper(),
                                       {"free": 0.0, "locked": 0.0})
        if from_locked:
            if slot_q["locked"] + 1e-12 < quote_used:
                raise ValueError("locked quote balance underflow")
            slot_q["locked"] = round(slot_q["locked"] - quote_used, 12)
        else:
            if slot_q["free"] + 1e-12 < quote_used:
                raise ValueError("free quote balance underflow")
            slot_q["free"] = round(slot_q["free"] - quote_used, 12)
        self.deposit(base_ccy, base_amt)

    def settle_sell(self, base_ccy: str, base_amt: float,
                    quote_ccy: str, quote_received: float,
                    *, from_locked: bool = False,
                    allow_negative_base: bool = False) -> None:
        """체결 후 정산: locked base → 차감, quote → 증가.

        ``allow_negative_base=True`` 이면 base 잔고가 음수가 되어도 허용 — short
        포지션 진입 모델링용. caller(MockBroker)가 ``config.allow_short`` 일 때 사용.
        """
        slot_b = self._book.setdefault(base_ccy.upper(),
                                       {"free": 0.0, "locked": 0.0})
        if from_locked:
            if slot_b["locked"] + 1e-12 < base_amt:
                raise ValueError("locked base balance underflow")
            slot_b["locked"] = round(slot_b["locked"] - base_amt, 12)
        else:
            if not allow_negative_base and slot_b["free"] + 1e-12 < base_amt:
                raise ValueError("free base balance underflow")
            slot_b["free"] = round(slot_b["free"] - base_amt, 12)
        self.deposit(quote_ccy, quote_received)

    def snapshot(self) -> dict[str, dict[str, float]]:
        """현재 잔고의 read-only 사본."""
        return {c: dict(v) for c, v in self._book.items()}


@dataclass
class _Position:
    qty: float = 0.0
    avg_entry_price: float = 0.0
    realized_pnl: float = 0.0


class MockPositionBook:
    """symbol → 포지션 매핑.

    avg_entry_price 는 BUY 누적 시 가중 평균. SELL 시 realized PnL 누적.
    qty 가 0 이 되면 avg_entry_price 도 0 으로 리셋 (포지션 close).
    """

    def __init__(self):
        self._positions: dict[str, _Position] = {}

    def get(self, symbol: str) -> _Position:
        return self._positions.setdefault(symbol.upper(), _Position())

    def all(self) -> dict[str, _Position]:
        return dict(self._positions)

    def on_buy(self, symbol: str, qty: float, price: float) -> None:
        p = self.get(symbol)
        if qty <= 0:
            return
        new_qty = p.qty + qty
        # 가중평균 — qty 가 0 일 수도 있고, 음수(short) 일 수도 있다.
        if p.qty <= 0:
            # 신규/숏 청산 — 단순화: 새 가격으로 평균 재설정 후 잔여 qty.
            if new_qty > 0:
                p.avg_entry_price = price
            elif new_qty == 0:
                p.avg_entry_price = 0.0
            # short 청산은 realized PnL 발생
            if p.qty < 0:
                cover_qty = min(qty, -p.qty)
                # 숏 PnL = entry - exit
                p.realized_pnl += (p.avg_entry_price - price) * cover_qty
        else:
            # 기존 롱 추가매수 — 가중평균
            p.avg_entry_price = (
                (p.avg_entry_price * p.qty + price * qty) / new_qty
            )
        p.qty = round(new_qty, 12)
        if abs(p.qty) < 1e-12:
            p.qty = 0.0
            p.avg_entry_price = 0.0

    def on_sell(self, symbol: str, qty: float, price: float) -> float:
        """SELL 체결 → realized PnL 변화량 반환."""
        p = self.get(symbol)
        if qty <= 0:
            return 0.0
        realized_delta = 0.0
        if p.qty > 0:
            # 롱 청산
            sell_qty = min(qty, p.qty)
            realized_delta = (price - p.avg_entry_price) * sell_qty
            p.realized_pnl += realized_delta
            p.qty = round(p.qty - sell_qty, 12)
            remainder = qty - sell_qty
            if abs(p.qty) < 1e-12:
                p.qty = 0.0
                p.avg_entry_price = 0.0
            # 남은 sell 은 short 진입
            if remainder > 0:
                p.qty = -remainder
                p.avg_entry_price = price
        else:
            # 신규 short 또는 short 추가
            new_qty = p.qty - qty   # qty 양수 → new_qty 더 음수
            if p.qty == 0:
                p.avg_entry_price = price
            else:
                # 가중평균 (short 누적)
                abs_old = -p.qty
                abs_new = -new_qty
                p.avg_entry_price = (
                    (p.avg_entry_price * abs_old + price * qty) / abs_new
                )
            p.qty = round(new_qty, 12)
        return realized_delta

    def unrealized_pnl(self, symbol: str, mark_price: float) -> float:
        p = self.get(symbol)
        if p.qty == 0 or mark_price <= 0:
            return 0.0
        if p.qty > 0:
            return (mark_price - p.avg_entry_price) * p.qty
        # short
        return (p.avg_entry_price - mark_price) * (-p.qty)


# ── 가격 / 마켓 ───────────────────────────────────────────────────


class MockMarket:
    """심볼별 mock 가격을 보관. 결정론적 fallback 가격(symbol 해시) 제공."""

    def __init__(self):
        self._prices: dict[str, float] = {}

    def set(self, symbol: str, price: float) -> None:
        if price <= 0:
            raise ValueError("mock market price must be > 0")
        self._prices[symbol.upper()] = float(price)

    def get(self, symbol: str) -> float:
        s = symbol.upper()
        if s in self._prices:
            return self._prices[s]
        # fallback — 결정론적 hash 기반 가격
        h = int(hashlib.md5(s.encode("utf-8")).hexdigest()[:8], 16)
        return 1000.0 + float(h % 100_000)


# ── Execution engine ─────────────────────────────────────────────


@dataclass(frozen=True)
class _FillCalc:
    fill_price: float
    fee: float
    slippage_pct: float


class MockExecutionEngine:
    """fee / slippage / fill price 계산. 결정론적 — random 사용 없음."""

    def __init__(self, config: MockBrokerConfig):
        self.config = config

    def calc_fill(
        self,
        *,
        side: str,
        ref_price: float,
    ) -> _FillCalc:
        # slippage 는 BUY 면 위로, SELL 이면 아래로.
        direction = 1 if side == "BUY" else -1
        slippage = self.config.slippage_bps / 10_000.0 * direction
        fill_price = ref_price * (1 + slippage)
        fee_rate = self.config.fee_bps / 10_000.0
        # fee 는 notional 기준 — 호출자가 fill_price * qty * fee_rate 로 계산.
        return _FillCalc(
            fill_price=round(fill_price, 12),
            fee=fee_rate,
            slippage_pct=abs(slippage) * 100,
        )


# ── 주문 book / Result 타입 ──────────────────────────────────────


@dataclass
class _OpenLimitOrder:
    order_id: str
    client_order_id: str
    symbol: str
    side: str
    price: float
    qty: float
    notional_usdt: float
    status: str = "OPEN"     # OPEN | FILLED | CANCELED | REJECTED


def _split_symbol(symbol: str, default_quote: str = "USDT") -> tuple[str, str]:
    """`BTC-USDT` / `BTCUSDT` / `BTC/USDT` → (base, quote)."""
    s = (symbol or "").strip().upper()
    if "/" in s:
        a, b = s.split("/", 1)
        return a, b
    if "-" in s:
        a, b = s.split("-", 1)
        return a, b
    # native 추정 — 알려진 quote 후미 분리
    for q in ("USDT", "USDC", "BUSD", "TUSD", "FDUSD", "KRW", "BTC", "ETH", "BNB"):
        if s.endswith(q) and len(s) > len(q):
            return s[:-len(q)], q
    return s, default_quote


def _safe_audit(order: dict, *, order_id: str) -> dict:
    out: dict[str, Any] = {"order_id": order_id}
    for k, v in (order or {}).items():
        kl = str(k).lower()
        if any(bad in kl for bad in _FORBIDDEN_RESPONSE_KEYS):
            continue
        out[k] = v
    return out


# ── MockBroker (top-level facade) ────────────────────────────────


class MockBroker:
    """실제 거래소 없이 주문 라이프사이클을 재현하는 시뮬레이션 브로커.

    주요 메서드:
      - place_order(request: dict) -> dict
      - cancel_order(order_id_or_client_id: str) -> dict
      - get_order(order_id_or_client_id: str) -> dict | None
      - get_balance(ccy: str | None = None) -> dict
      - get_position(symbol: str) -> dict
      - get_account_summary() -> dict
      - set_market_price(symbol, price) / mark_price(symbol, price) — 동의어
      - reset() — 전체 상태 초기화

    결과 dict 의 필수 키:
      - status, route, symbol, side, order_id, filled_price, notional_usdt,
        fee_usdt, slippage_pct, reason
      - mode (=config.mode, MOCK/PAPER)
      - is_real_trade=False
      - execution_source="mock_broker"
      - warning=Mock 안내 문구
      - audit (secret sanitize)
    """

    DEFAULT_QUOTE = "USDT"

    def __init__(self, config: MockBrokerConfig | None = None):
        self.config = config or MockBrokerConfig()
        self.account = MockAccountState(self.config.initial_balances)
        self.positions = MockPositionBook()
        self.market = MockMarket()
        self.engine = MockExecutionEngine(self.config)

        self._by_client_id: dict[str, dict] = {}
        self._open_orders: dict[str, _OpenLimitOrder] = {}
        self._closed_orders: dict[str, _OpenLimitOrder] = {}
        # 통계 (관제/디버그용)
        self._filled_count = 0
        self._rejected_count = 0

    # ── 공개 — config / 시세 ───────────────────────────────────────

    def set_market_price(self, symbol: str, price: float) -> None:
        self.market.set(symbol, price)

    # alias — 외부 인터페이스 호환
    mark_price = set_market_price

    def reset(self) -> None:
        self.account = MockAccountState(self.config.initial_balances)
        self.positions = MockPositionBook()
        self.market = MockMarket()
        self._by_client_id.clear()
        self._open_orders.clear()
        self._closed_orders.clear()
        self._filled_count = 0
        self._rejected_count = 0

    # ── 공개 — 조회 ────────────────────────────────────────────────

    def get_balance(self, ccy: str | None = None) -> dict:
        snap = self.account.snapshot()
        if ccy is None:
            return {
                "balances": snap,
                "mode": self.config.mode,
                "is_real_trade": False,
                "execution_source": "mock_broker",
                "warning": _MOCK_WARNING,
            }
        c = ccy.upper()
        slot = snap.get(c, {"free": 0.0, "locked": 0.0})
        return {
            "ccy": c,
            "free": slot["free"],
            "locked": slot["locked"],
            "total": slot["free"] + slot["locked"],
            "mode": self.config.mode,
            "is_real_trade": False,
            "execution_source": "mock_broker",
            "warning": _MOCK_WARNING,
        }

    def get_position(self, symbol: str) -> dict:
        s = symbol.upper()
        p = self.positions.get(s)
        mark = self.market.get(s)
        return {
            "symbol": s,
            "qty": p.qty,
            "avg_entry_price": p.avg_entry_price,
            "realized_pnl": p.realized_pnl,
            "unrealized_pnl": self.positions.unrealized_pnl(s, mark),
            "mark_price": mark,
            "mode": self.config.mode,
            "is_real_trade": False,
            "execution_source": "mock_broker",
        }

    def get_order(self, order_id: str) -> dict | None:
        if not order_id:
            return None
        # client_order_id 우선 — 결과 dict 가 들어있음
        if order_id in self._by_client_id:
            return dict(self._by_client_id[order_id])
        # exchange order_id
        o = self._open_orders.get(order_id) or self._closed_orders.get(order_id)
        if o is None:
            return None
        return asdict(o)

    def get_account_summary(self) -> dict:
        snap = self.account.snapshot()
        positions = {}
        for s, p in self.positions.all().items():
            mark = self.market.get(s)
            positions[s] = {
                "qty": p.qty,
                "avg_entry_price": p.avg_entry_price,
                "realized_pnl": p.realized_pnl,
                "unrealized_pnl": self.positions.unrealized_pnl(s, mark),
                "mark_price": mark,
            }
        return {
            "balances": snap,
            "positions": positions,
            "open_orders": [asdict(o) for o in self._open_orders.values()],
            "filled_count": self._filled_count,
            "rejected_count": self._rejected_count,
            "config": {
                "mode": self.config.mode,
                "base_currency": self.config.base_currency,
                "fee_bps": self.config.fee_bps,
                "slippage_bps": self.config.slippage_bps,
                "allow_short": self.config.allow_short,
                "allow_margin": self.config.allow_margin,
                "max_order_notional": self.config.max_order_notional,
            },
            "mode": self.config.mode,
            "is_real_trade": False,
            "execution_source": "mock_broker",
            "warning": _MOCK_WARNING,
        }

    # ── 공개 — 주문 ────────────────────────────────────────────────

    def place_order(self, request: dict) -> dict:
        order_dict = dict(request or {})
        symbol = str(order_dict.get("symbol") or "").upper()
        side = str(order_dict.get("side") or "BUY").upper()
        order_type = str(
            order_dict.get("order_type") or "MARKET"
        ).upper()
        notional = float(order_dict.get("notional_usdt") or 0)
        qty_req = float(order_dict.get("size") or order_dict.get("qty") or 0)
        ref_price = float(order_dict.get("price") or 0)
        client_id = str(
            order_dict.get("client_order_id")
            or order_dict.get("idempotency_key")
            or ""
        )

        # 1) LIVE mode 거부 (mock broker 정책 — 영구).
        requested_mode = str(
            order_dict.get("mode") or order_dict.get("trading_mode") or ""
        ).upper()
        if requested_mode == "LIVE":
            return self._reject(
                order_dict, client_id,
                reason="MockBroker: LIVE mode order rejected (mock-only environment)",
                route="live_not_wired",
            )

        # 2) duplicate client_order_id → 우선순위:
        #    - 첫 결과가 REJECTED 인 경우에도 두 번째 호출은 그 REJECTED 를 그대로 반환.
        #    - 결제 성공된 경우에도 동일 (idempotent).
        if client_id and client_id in self._by_client_id:
            return dict(self._by_client_id[client_id])

        # 3) 입력 검증.
        if not symbol or "-" not in (symbol.replace("/", "-")):
            # /, - 둘 다 안 들어왔으면 native 형식 → 분리 시도 후에도 단일 토큰이면 reject.
            base, quote = _split_symbol(symbol, default_quote=self.config.base_currency)
            if not base or not quote or base == symbol:
                # native 분리 실패 케이스만 reject
                if "-" not in symbol and "/" not in symbol and base == symbol:
                    return self._reject(
                        order_dict, client_id,
                        reason=f"invalid symbol: {symbol!r}",
                    )
        base_ccy, quote_ccy = _split_symbol(
            symbol, default_quote=self.config.base_currency,
        )
        if not base_ccy or not quote_ccy:
            return self._reject(
                order_dict, client_id,
                reason=f"invalid symbol: {symbol!r}",
            )

        if side not in ("BUY", "SELL"):
            return self._reject(
                order_dict, client_id,
                reason=f"unsupported side: {side!r} (BUY/SELL only)",
            )
        if order_type not in ("MARKET", "LIMIT"):
            return self._reject(
                order_dict, client_id,
                reason=f"unknown order_type: {order_type}",
            )
        if order_type == "LIMIT" and ref_price <= 0:
            return self._reject(
                order_dict, client_id,
                reason="LIMIT order requires price > 0",
            )

        # 4) max_order_notional 한도.
        if self.config.max_order_notional > 0 and notional > 0 \
                and notional > self.config.max_order_notional:
            return self._reject(
                order_dict, client_id,
                reason=(f"order notional {notional} exceeds "
                        f"max_order_notional={self.config.max_order_notional}"),
            )

        # 5) 가격 결정.
        market_price = self.market.get(symbol)
        if order_type == "MARKET":
            base_price = market_price
        else:
            base_price = ref_price

        # 6) qty 결정 — notional 우선, 없으면 qty.
        if notional > 0 and qty_req <= 0:
            if base_price <= 0:
                return self._reject(
                    order_dict, client_id,
                    reason="cannot derive qty: market price unavailable",
                )
            qty = notional / base_price
        elif qty_req > 0:
            qty = qty_req
            if notional <= 0:
                notional = qty * base_price
        else:
            return self._reject(
                order_dict, client_id,
                reason="notional_usdt or size must be > 0",
            )

        # 7) execution.
        fill = self.engine.calc_fill(side=side, ref_price=base_price)
        fee_rate = fill.fee
        fill_price = fill.fill_price if order_type == "MARKET" else base_price

        if order_type == "MARKET":
            return self._fill_market(
                order_dict=order_dict, client_id=client_id,
                symbol=symbol, base_ccy=base_ccy, quote_ccy=quote_ccy,
                side=side, qty=qty, fill_price=fill_price, fee_rate=fee_rate,
                slippage_pct=fill.slippage_pct,
            )

        # LIMIT
        return self._place_limit(
            order_dict=order_dict, client_id=client_id,
            symbol=symbol, base_ccy=base_ccy, quote_ccy=quote_ccy,
            side=side, qty=qty, limit_price=ref_price,
            market_price=market_price, fee_rate=fee_rate,
        )

    def cancel_order(self, order_id_or_client_id: str) -> dict:
        oid = (order_id_or_client_id or "").strip()
        # client_id 로 찾기
        target_oid: str | None = None
        if oid in self._open_orders:
            target_oid = oid
        else:
            # client_id 매핑 추적
            for o in self._open_orders.values():
                if o.client_order_id == oid:
                    target_oid = o.order_id
                    break
        if target_oid is None:
            return {
                "status": "REJECTED",
                "route": "paper",
                "order_id": oid,
                "reason": "unknown order_id (not open)",
                "mode": self.config.mode,
                "is_real_trade": False,
                "execution_source": "mock_broker",
                "warning": _MOCK_WARNING,
            }
        o = self._open_orders.pop(target_oid)
        # locked balance 해제
        base_ccy, quote_ccy = _split_symbol(
            o.symbol, default_quote=self.config.base_currency,
        )
        if o.side == "BUY":
            self.account.unlock(quote_ccy, o.notional_usdt)
        else:
            self.account.unlock(base_ccy, o.qty)
        o.status = "CANCELED"
        self._closed_orders[target_oid] = o
        return {
            "status": "ACCEPTED",
            "route": "paper",
            "symbol": o.symbol,
            "side": o.side,
            "order_id": target_oid,
            "reason": "mock cancel",
            "mode": self.config.mode,
            "is_real_trade": False,
            "execution_source": "mock_broker",
            "warning": _MOCK_WARNING,
        }

    # ── 내부 — MARKET 즉시 체결 ───────────────────────────────────

    def _fill_market(
        self, *,
        order_dict: dict, client_id: str,
        symbol: str, base_ccy: str, quote_ccy: str,
        side: str, qty: float,
        fill_price: float, fee_rate: float,
        slippage_pct: float,
    ) -> dict:
        if fill_price <= 0:
            return self._reject(
                order_dict, client_id,
                reason="market price unavailable",
            )
        notional = qty * fill_price
        fee = notional * fee_rate

        # 잔고 검증.
        if side == "BUY":
            if not self.config.allow_margin:
                if self.account.free(quote_ccy) + 1e-12 < (notional + fee):
                    return self._reject(
                        order_dict, client_id,
                        reason=(f"insufficient_balance: have "
                                f"{self.account.free(quote_ccy):.6f} {quote_ccy}, "
                                f"need {notional + fee:.6f} {quote_ccy}"),
                    )
            self.account.settle_buy(
                base_ccy=base_ccy, base_amt=qty,
                quote_ccy=quote_ccy, quote_used=notional + fee,
            )
            self.positions.on_buy(symbol, qty, fill_price)
        else:  # SELL
            if not self.config.allow_short:
                if self.account.free(base_ccy) + 1e-12 < qty:
                    return self._reject(
                        order_dict, client_id,
                        reason=(f"insufficient_base_balance: have "
                                f"{self.account.free(base_ccy):.6f} {base_ccy}, "
                                f"need {qty:.6f} {base_ccy}"),
                    )
            self.account.settle_sell(
                base_ccy=base_ccy, base_amt=qty,
                quote_ccy=quote_ccy, quote_received=notional - fee,
                allow_negative_base=self.config.allow_short,
            )
            self.positions.on_sell(symbol, qty, fill_price)

        # market price 자동 갱신 (체결가 반영).
        self.market.set(symbol, fill_price)

        self._filled_count += 1
        order_id = f"mock-{uuid4().hex[:10]}"
        result = {
            "status": "FILLED",
            "route": "paper",
            "symbol": symbol, "side": side,
            "order_id": order_id,
            "filled_price": fill_price,
            "qty": qty,
            "notional_usdt": notional,
            "fee_usdt": fee,
            "slippage_pct": slippage_pct,
            "reason": "mock fill (MARKET)",
            "mode": self.config.mode,
            "is_real_trade": False,
            "execution_source": "mock_broker",
            "warning": _MOCK_WARNING,
            "audit": _safe_audit(order_dict, order_id=order_id),
        }
        if client_id:
            self._by_client_id[client_id] = result
        return result

    # ── 내부 — LIMIT 처리 ─────────────────────────────────────────

    def _place_limit(
        self, *,
        order_dict: dict, client_id: str,
        symbol: str, base_ccy: str, quote_ccy: str,
        side: str, qty: float,
        limit_price: float, market_price: float, fee_rate: float,
    ) -> dict:
        # 즉시 체결 조건:
        #   BUY  : 시장가 <= limit (limit 가 시장가 이상)
        #   SELL : 시장가 >= limit (limit 가 시장가 이하)
        crossable = (
            (side == "BUY" and market_price > 0 and market_price <= limit_price)
            or (side == "SELL" and market_price > 0 and market_price >= limit_price)
        )
        if crossable:
            return self._fill_market(
                order_dict=order_dict, client_id=client_id,
                symbol=symbol, base_ccy=base_ccy, quote_ccy=quote_ccy,
                side=side, qty=qty, fill_price=limit_price, fee_rate=fee_rate,
                slippage_pct=0.0,
            )

        # 미체결 → open. 잔고 잠금.
        notional = qty * limit_price
        fee_reserve = notional * fee_rate
        if side == "BUY":
            need = notional + fee_reserve
            if not self.config.allow_margin and self.account.free(quote_ccy) + 1e-12 < need:
                return self._reject(
                    order_dict, client_id,
                    reason=(f"insufficient_balance to lock {need:.6f} {quote_ccy} "
                            f"(free={self.account.free(quote_ccy):.6f})"),
                )
            self.account.lock(quote_ccy, need)
            locked_notional = need
        else:
            if not self.config.allow_short:
                if self.account.free(base_ccy) + 1e-12 < qty:
                    return self._reject(
                        order_dict, client_id,
                        reason=(f"insufficient_base_balance to lock {qty:.6f} {base_ccy} "
                                f"(free={self.account.free(base_ccy):.6f})"),
                    )
            self.account.lock(base_ccy, qty)
            locked_notional = notional  # 정보용

        order_id = f"mock-{uuid4().hex[:10]}"
        o = _OpenLimitOrder(
            order_id=order_id,
            client_order_id=client_id,
            symbol=symbol, side=side,
            price=limit_price, qty=qty,
            notional_usdt=locked_notional,
            status="OPEN",
        )
        self._open_orders[order_id] = o
        result = {
            "status": "ACCEPTED",
            "route": "paper",
            "symbol": symbol, "side": side,
            "order_id": order_id,
            "filled_price": 0.0,
            "qty": qty,
            "notional_usdt": locked_notional,
            "fee_usdt": 0.0,
            "slippage_pct": 0.0,
            "reason": "mock open (LIMIT, not crossable)",
            "mode": self.config.mode,
            "is_real_trade": False,
            "execution_source": "mock_broker",
            "warning": _MOCK_WARNING,
            "audit": _safe_audit(order_dict, order_id=order_id),
        }
        if client_id:
            self._by_client_id[client_id] = result
        return result

    # ── 내부 — reject ──────────────────────────────────────────────

    def _reject(
        self,
        order_dict: dict,
        client_id: str,
        *,
        reason: str,
        route: str = "paper",
    ) -> dict:
        self._rejected_count += 1
        r = {
            "status": "REJECTED",
            "route": route,
            "symbol": str(order_dict.get("symbol") or ""),
            "side": str(order_dict.get("side") or ""),
            "order_id": "",
            "filled_price": 0.0,
            "qty": 0.0,
            "notional_usdt": float(order_dict.get("notional_usdt") or 0),
            "fee_usdt": 0.0,
            "slippage_pct": 0.0,
            "reason": reason,
            "mode": self.config.mode,
            "is_real_trade": False,
            "execution_source": "mock_broker",
            "warning": _MOCK_WARNING,
            "audit": {"reason_code": "rejected"},
        }
        if client_id:
            self._by_client_id[client_id] = r
        return r


__all__ = (
    "MockBrokerConfig",
    "MockAccountState",
    "MockPositionBook",
    "MockMarket",
    "MockExecutionEngine",
    "MockBroker",
)
