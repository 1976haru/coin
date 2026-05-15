"""Exchange Notices — 체크리스트 #18.

거래소 공지(입출금 중단, 상장폐지, 유의종목, 점검)를 메모리 레지스트리에
보관하고 심볼별 거래 가능 여부를 평가한다.

설계 원칙:
  - 이 모듈은 read-only 데이터 모델 + 평가 함수 + 메모리 캐시
  - 실제 거래소 RSS/API 폴링은 #21·#22 (Exchange Adapter) 또는 별도 worker에서
  - DB 영속화는 후속 PR에서 (Notice.to_dict/from_dict 직렬화 헬퍼 제공)

KimpStrategy 호환:
  - assess_symbol_notices().deposit_withdrawal_ok → KimpStrategy 의 동명 입력
  - assess_symbol_notices().tradable → 상폐/점검 차단
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Iterable, Literal


# ── 타입 ──────────────────────────────────────────────────────────

NoticeKind = Literal[
    "DEPOSIT_SUSPENDED",
    "WITHDRAWAL_SUSPENDED",
    "BOTH_SUSPENDED",
    "DELISTING",
    "WARNING",
    "MAINTENANCE",
]

NoticeSeverity = Literal["block", "warn"]


_KIND_TO_SEVERITY: dict[str, NoticeSeverity] = {
    "DEPOSIT_SUSPENDED":    "block",
    "WITHDRAWAL_SUSPENDED": "block",
    "BOTH_SUSPENDED":       "block",
    "DELISTING":            "block",
    "MAINTENANCE":          "block",
    "WARNING":              "warn",
}


@dataclass(frozen=True)
class Notice:
    """단일 거래소 공지.

    starts_at 이상 ends_at 미만 구간에서 active. ends_at=None 이면 open-ended.
    """

    id:         int
    exchange:   str
    symbol:     str
    kind:       NoticeKind
    message:    str
    starts_at:  datetime
    ends_at:    datetime | None = None
    source_url: str = ""

    @property
    def severity(self) -> NoticeSeverity:
        return _KIND_TO_SEVERITY.get(self.kind, "warn")

    def is_active(self, now: datetime | None = None) -> bool:
        now = (now or datetime.now(timezone.utc))
        if now < self.starts_at:
            return False
        if self.ends_at is not None and now >= self.ends_at:
            return False
        return True

    def to_dict(self) -> dict:
        d = asdict(self)
        d["starts_at"] = self.starts_at.isoformat()
        d["ends_at"]   = self.ends_at.isoformat() if self.ends_at else None
        d["severity"]  = self.severity
        return d


@dataclass(frozen=True)
class SymbolNoticeStatus:
    """단일 (symbol, exchange) 의 거래 가능성 요약.

    KimpStrategy 등 전략이 직접 소비할 수 있는 플래그 형태.
    """

    symbol: str
    exchange: str
    deposit_ok: bool
    withdrawal_ok: bool
    tradable: bool          # False = 상폐/점검 (어떤 거래도 금지)
    has_warning: bool       # 유의종목 (거래는 가능하나 경고)
    active_notices: tuple[Notice, ...] = field(default_factory=tuple)

    @property
    def deposit_withdrawal_ok(self) -> bool:
        """KimpStrategy.generate_signal(deposit_withdrawal_ok=...) 직결."""
        return self.deposit_ok and self.withdrawal_ok

    def reasons(self) -> list[str]:
        return [f"[{n.kind}] {n.message}" for n in self.active_notices]


# ── 레지스트리 ────────────────────────────────────────────────────

class NoticeRegistry:
    """거래소 공지 메모리 레지스트리.

    add/remove/list/active_for. id 는 자동 증가.
    DB 영속은 후속 PR; 메모리/CSV 양쪽에 동등 사용 가능.
    """

    def __init__(self):
        self._next_id = 1
        self._items: dict[int, Notice] = {}

    def add(
        self,
        exchange: str,
        symbol: str,
        kind: NoticeKind,
        message: str,
        starts_at: datetime | None = None,
        ends_at: datetime | None = None,
        source_url: str = "",
    ) -> Notice:
        if kind not in _KIND_TO_SEVERITY:
            raise ValueError(f"unknown notice kind: {kind}")
        notice = Notice(
            id=self._next_id,
            exchange=exchange,
            symbol=symbol,
            kind=kind,
            message=message,
            starts_at=(starts_at or datetime.now(timezone.utc)),
            ends_at=ends_at,
            source_url=source_url,
        )
        self._items[notice.id] = notice
        self._next_id += 1
        return notice

    def remove(self, notice_id: int) -> bool:
        return self._items.pop(notice_id, None) is not None

    def get(self, notice_id: int) -> Notice | None:
        return self._items.get(notice_id)

    def all(self) -> list[Notice]:
        return list(self._items.values())

    def active(self, now: datetime | None = None) -> list[Notice]:
        return [n for n in self._items.values() if n.is_active(now)]

    def active_for(
        self, symbol: str, exchange: str, now: datetime | None = None,
    ) -> list[Notice]:
        return [
            n for n in self._items.values()
            if n.symbol == symbol and n.exchange == exchange and n.is_active(now)
        ]

    def clear(self) -> None:
        self._items.clear()
        self._next_id = 1


# ── 평가 함수 ────────────────────────────────────────────────────

def assess_symbol_notices(
    registry: NoticeRegistry,
    symbol: str,
    exchange: str,
    now: datetime | None = None,
) -> SymbolNoticeStatus:
    """심볼·거래소에 대한 active 공지를 합산해 거래 가능성 플래그를 산출."""
    actives = registry.active_for(symbol, exchange, now)

    deposit_ok    = True
    withdrawal_ok = True
    tradable      = True
    has_warning   = False

    for n in actives:
        if n.kind == "DEPOSIT_SUSPENDED":
            deposit_ok = False
        elif n.kind == "WITHDRAWAL_SUSPENDED":
            withdrawal_ok = False
        elif n.kind == "BOTH_SUSPENDED":
            deposit_ok = False
            withdrawal_ok = False
        elif n.kind == "DELISTING":
            tradable = False
            deposit_ok = False
            withdrawal_ok = False
        elif n.kind == "MAINTENANCE":
            tradable = False
        elif n.kind == "WARNING":
            has_warning = True

    return SymbolNoticeStatus(
        symbol=symbol,
        exchange=exchange,
        deposit_ok=deposit_ok,
        withdrawal_ok=withdrawal_ok,
        tradable=tradable,
        has_warning=has_warning,
        active_notices=tuple(actives),
    )


def block_reasons(
    registry: NoticeRegistry,
    targets: Iterable[tuple[str, str]],
    now: datetime | None = None,
) -> list[str]:
    """다중 (symbol, exchange) 에 대해 BUY 차단 사유 문자열 리스트.

    하나라도 거래 불가/입출금 중단이면 차단 사유로 추가. RiskManager·OrderGateway
    의 freshness_block_reasons 와 같은 형식으로 사용 가능.
    """
    reasons: list[str] = []
    for symbol, exchange in targets:
        s = assess_symbol_notices(registry, symbol, exchange, now)
        if not s.tradable:
            reasons.append(f"{exchange}:{symbol} 거래 불가 (상폐/점검)")
        elif not s.deposit_withdrawal_ok:
            reasons.append(
                f"{exchange}:{symbol} 입출금 중단 "
                f"(deposit={s.deposit_ok}, withdrawal={s.withdrawal_ok})"
            )
    return reasons
