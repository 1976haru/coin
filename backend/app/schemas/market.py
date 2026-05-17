"""시장 데이터 스키마 (frozen dataclass).

이전 위치: app/market/models.py — 체크리스트 #8에 따라 schemas/로 이동.
"""
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Ticker:
    symbol: str
    price: float
    bid: float
    ask: float
    spread_pct: float
    volume_24h: float
    ts: datetime

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2


@dataclass(frozen=True)
class OHLCV:
    symbol: str
    timeframe: str
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class KimpSnapshot:
    symbol: str
    upbit_price_krw: float
    okx_price_usdt: float
    usdt_krw: float
    kimp_pct: float
    ts: datetime
    deposit_ok: bool = True
    withdrawal_ok: bool = True

    @staticmethod
    def compute_kimp(upbit_krw: float, okx_usdt: float, fx: float) -> float:
        """체크리스트 #34: 단일 진리 소스(`app.market.kimp.compute_kimp_pct`)에 위임.

        비정상 입력은 0.0 반환 (legacy 동작 유지).
        """
        from app.market.kimp import compute_kimp_pct
        return compute_kimp_pct(upbit_krw, okx_usdt, fx, strict=False)


@dataclass(frozen=True)
class OrderBook:
    symbol: str
    bids: tuple
    asks: tuple
    ts: datetime

    def best_bid(self) -> float:
        return float(self.bids[0][0]) if self.bids else 0.0

    def best_ask(self) -> float:
        return float(self.asks[0][0]) if self.asks else 0.0

    def spread_pct(self) -> float:
        bid, ask = self.best_bid(), self.best_ask()
        return (ask - bid) / bid if bid > 0 else 0.0

    def bid_depth_usdt(self, levels: int = 5) -> float:
        return sum(float(p) * float(q) for p, q in self.bids[:levels])


# 체크리스트 #15 Market Data Collector — Funding / FX
#
# 본 단계의 데이터 흐름은 모두 read-only / Mock 우선. 실거래 호출 없음.

@dataclass(frozen=True)
class FundingRate:
    """파생상품(perpetual) funding rate. spot-only 거래소는 None 으로 처리."""
    symbol: str
    exchange: str
    funding_rate: float
    ts: datetime
    next_funding_time: datetime | None = None


@dataclass(frozen=True)
class FxRate:
    """환율 (예: USDT-KRW). 거래소가 아닌 외부 source 사용."""
    pair: str           # e.g. "USDT-KRW"
    rate: float
    ts: datetime
    source: str = "mock"
