"""WatchlistService — 거래 universe 관리. 체크리스트 #14.

설계 원칙 (`docs/watchlist_universe.md` 참조):
  - Watchlist 는 **주문 허용 목록이 아니라 후보 universe 제한 장치**.
    여기에 있어도 RiskManager/OrderGuard/PermissionGate 를 그대로 통과해야 한다.
  - 전체 시장 자동 스캔 금지. 초기 universe 크기는 20~100 수준.
  - 거래소 API 호출 없음. 단순 DB 모델 + 검증 + 제한.

서비스 책임:
  1. 정규화: symbol upper, exchange/list_name lower, strip
  2. 검증: 빈/공백/길이/허용 거래소
  3. universe 크기 제한: list_name 별 cap + 전체 enabled cap
  4. CoinSymbol 마스터(#13)와 역할이 다름 — universe 는 "분석·수집·전략 후보" 셋.
"""
from __future__ import annotations
from collections import defaultdict
from typing import Sequence

from sqlalchemy import select, delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import WatchlistEntry


# ── 상수 ───────────────────────────────────────────────────────────
ALLOWED_EXCHANGES: frozenset[str] = frozenset({
    "upbit", "binance", "okx", "mock", "paper",
})
MAX_SYMBOL_LENGTH   = 32
MAX_EXCHANGE_LENGTH = 16
MAX_LIST_NAME_LENGTH = 32

# list_name 별 enabled cap. 초기 universe 20~100 가이드라인 (`docs/watchlist_universe.md`).
DEFAULT_LIST_LIMITS: dict[str, int] = {
    "default":    50,
    "majors":     20,
    "kimp_pairs": 100,
}
DEFAULT_OTHER_LIST_LIMIT = 50


# ── 예외 ───────────────────────────────────────────────────────────
class WatchlistDuplicateError(ValueError):
    """동일 (list_name, symbol, exchange) 조합이 이미 존재."""


class WatchlistNotFoundError(LookupError):
    """id로 항목을 찾을 수 없음."""


class WatchlistValidationError(ValueError):
    """입력 검증 실패 (빈/공백/너무 긴 symbol / 허용 외 exchange 등)."""


class WatchlistLimitError(ValueError):
    """universe 크기 제한 초과 — list_name 별 cap 또는 전체 enabled cap."""


# ── 정규화 헬퍼 ────────────────────────────────────────────────────
def _normalize_symbol(s: str) -> str:
    return (s or "").strip().upper()


def _normalize_exchange(s: str) -> str:
    return (s or "").strip().lower()


def _normalize_list_name(s: str) -> str:
    return (s or "").strip().lower()


def _validate(symbol: str, exchange: str, list_name: str) -> None:
    """정규화 *후* 값에 대한 검증. 위반 시 WatchlistValidationError."""
    if not symbol:
        raise WatchlistValidationError("symbol must not be empty")
    if any(c.isspace() for c in symbol):
        raise WatchlistValidationError(f"symbol must not contain whitespace: {symbol!r}")
    if len(symbol) > MAX_SYMBOL_LENGTH:
        raise WatchlistValidationError(
            f"symbol too long ({len(symbol)} > {MAX_SYMBOL_LENGTH}): {symbol!r}"
        )
    if not exchange:
        raise WatchlistValidationError("exchange must not be empty")
    if exchange not in ALLOWED_EXCHANGES:
        raise WatchlistValidationError(
            f"exchange {exchange!r} not in allowed set {sorted(ALLOWED_EXCHANGES)}"
        )
    if len(exchange) > MAX_EXCHANGE_LENGTH:
        raise WatchlistValidationError(
            f"exchange too long ({len(exchange)} > {MAX_EXCHANGE_LENGTH}): {exchange!r}"
        )
    if not list_name:
        raise WatchlistValidationError("list_name must not be empty")
    if any(c.isspace() for c in list_name):
        raise WatchlistValidationError(
            f"list_name must not contain whitespace: {list_name!r}"
        )
    if len(list_name) > MAX_LIST_NAME_LENGTH:
        raise WatchlistValidationError(
            f"list_name too long ({len(list_name)} > {MAX_LIST_NAME_LENGTH}): {list_name!r}"
        )


class WatchlistService:
    """거래 universe CRUD + 정규화/검증/크기 제한.

    여러 목록(`list_name`)을 동시에 운영 가능 (예: "kimp_pairs", "majors").

    제한 동작:
      - `add(enabled=True)` 또는 `set_enabled(id, True)` 호출 시,
        해당 list_name 의 enabled 항목 수 + 1 이 list_limit 을 넘으면 LimitError.
      - 전체 enabled 합 + 1 이 `max_enabled_total` 을 넘으면 LimitError.
      - `enabled=False` 항목은 cap 계산에서 제외.

    `max_enabled_total` 은 인스턴스 인자 → 그것이 None 이면
    `Settings.watchlist_max_enabled_total` (env: `WATCHLIST_MAX_ENABLED_TOTAL`).
    """

    def __init__(
        self,
        session: Session,
        *,
        max_enabled_total: int | None = None,
        list_limits: dict[str, int] | None = None,
        other_list_limit: int | None = None,
    ):
        self.s = session
        self._max_enabled_total_override = max_enabled_total
        self.list_limits: dict[str, int] = {
            **DEFAULT_LIST_LIMITS, **(list_limits or {}),
        }
        self.other_list_limit = (
            other_list_limit if other_list_limit is not None
            else DEFAULT_OTHER_LIST_LIMIT
        )

    # ── 한도 해석 ─────────────────────────────────────────────────
    def _max_enabled_total(self) -> int:
        if self._max_enabled_total_override is not None:
            return self._max_enabled_total_override
        try:
            from app.core.config import get_settings
            return int(get_settings().watchlist_max_enabled_total)
        except Exception:
            return 100  # 안전 기본값

    def limit_for(self, list_name: str) -> int:
        return self.list_limits.get(list_name, self.other_list_limit)

    # ── Read ──────────────────────────────────────────────────────

    def list_entries(
        self,
        list_name: str | None = None,
        exchange: str | None = None,
        enabled_only: bool = False,
    ) -> list[dict]:
        stmt = select(WatchlistEntry)
        if list_name is not None:
            stmt = stmt.where(WatchlistEntry.list_name == _normalize_list_name(list_name))
        if exchange is not None:
            stmt = stmt.where(WatchlistEntry.exchange == _normalize_exchange(exchange))
        if enabled_only:
            stmt = stmt.where(WatchlistEntry.enabled.is_(True))
        stmt = stmt.order_by(WatchlistEntry.list_name, WatchlistEntry.symbol)
        rows = self.s.execute(stmt).scalars().all()
        return [self._to_dict(r) for r in rows]

    def get_by_id(self, entry_id: int) -> dict:
        row = self.s.get(WatchlistEntry, entry_id)
        if row is None:
            raise WatchlistNotFoundError(f"watchlist id={entry_id} not found")
        return self._to_dict(row)

    def count(self, list_name: str | None = None, enabled_only: bool = False) -> int:
        stmt = select(WatchlistEntry)
        if list_name is not None:
            stmt = stmt.where(WatchlistEntry.list_name == _normalize_list_name(list_name))
        if enabled_only:
            stmt = stmt.where(WatchlistEntry.enabled.is_(True))
        return len(self.s.execute(stmt).scalars().all())

    def list_names(self) -> list[str]:
        stmt = select(WatchlistEntry.list_name).distinct().order_by(WatchlistEntry.list_name)
        return [name for (name,) in self.s.execute(stmt).all()]

    def summary(self) -> dict:
        """운영 요약: 총·enabled·disabled 합, by_exchange, by_list_name, limits."""
        rows = self.s.execute(select(WatchlistEntry)).scalars().all()
        by_exchange: dict[str, int] = defaultdict(int)
        by_list_name: dict[str, int] = defaultdict(int)
        enabled = 0
        for r in rows:
            if r.enabled:
                by_exchange[r.exchange] += 1
                by_list_name[r.list_name] += 1
                enabled += 1
        return {
            "total":    len(rows),
            "enabled":  enabled,
            "disabled": len(rows) - enabled,
            "by_exchange":  dict(by_exchange),
            "by_list_name": dict(by_list_name),
            "limits": {
                **self.list_limits,
                "other":             self.other_list_limit,
                "max_enabled_total": self._max_enabled_total(),
            },
        }

    # ── Write ─────────────────────────────────────────────────────

    def add(
        self,
        symbol: str,
        exchange: str = "upbit",
        list_name: str = "default",
        enabled: bool = True,
        max_notional_usdt_override: float | None = None,
        tags: Sequence[str] | None = None,
        note: str = "",
    ) -> dict:
        symbol_n    = _normalize_symbol(symbol)
        exchange_n  = _normalize_exchange(exchange)
        list_name_n = _normalize_list_name(list_name)
        _validate(symbol_n, exchange_n, list_name_n)

        if enabled:
            self._enforce_enabled_limits(list_name_n, delta=1)

        entry = WatchlistEntry(
            list_name=list_name_n,
            symbol=symbol_n,
            exchange=exchange_n,
            enabled=enabled,
            max_notional_usdt_override=max_notional_usdt_override,
            tags=list(tags or []),
            note=note,
        )
        self.s.add(entry)
        try:
            self.s.commit()
        except IntegrityError as e:
            self.s.rollback()
            raise WatchlistDuplicateError(
                f"({list_name_n}, {symbol_n}, {exchange_n}) already exists"
            ) from e
        self.s.refresh(entry)
        return self._to_dict(entry)

    def remove(self, entry_id: int) -> None:
        row = self.s.get(WatchlistEntry, entry_id)
        if row is None:
            raise WatchlistNotFoundError(f"watchlist id={entry_id} not found")
        self.s.delete(row)
        self.s.commit()

    def set_enabled(self, entry_id: int, enabled: bool) -> dict:
        row = self.s.get(WatchlistEntry, entry_id)
        if row is None:
            raise WatchlistNotFoundError(f"watchlist id={entry_id} not found")
        # OFF → ON 전환 시 cap 검증. 이미 ON 또는 OFF→OFF 는 면제.
        if enabled and not row.enabled:
            self._enforce_enabled_limits(row.list_name, delta=1)
        row.enabled = enabled
        self.s.commit()
        self.s.refresh(row)
        return self._to_dict(row)

    def remove_by_list(self, list_name: str) -> int:
        """list_name 전체 제거. 반환값: 삭제된 행 수."""
        list_name_n = _normalize_list_name(list_name)
        stmt = delete(WatchlistEntry).where(WatchlistEntry.list_name == list_name_n)
        result = self.s.execute(stmt)
        self.s.commit()
        return int(result.rowcount or 0)

    # ── Limit enforcement ─────────────────────────────────────────

    def _enforce_enabled_limits(self, list_name: str, *, delta: int) -> None:
        """delta 만큼의 신규 enabled 가 cap 을 넘으면 WatchlistLimitError."""
        list_cap = self.limit_for(list_name)
        cur_in_list = self.count(list_name=list_name, enabled_only=True)
        if cur_in_list + delta > list_cap:
            raise WatchlistLimitError(
                f"list_name={list_name!r} enabled cap exceeded: "
                f"{cur_in_list}+{delta} > {list_cap}"
            )
        total_cap = self._max_enabled_total()
        cur_total = self.count(enabled_only=True)
        if cur_total + delta > total_cap:
            raise WatchlistLimitError(
                f"total enabled cap exceeded: "
                f"{cur_total}+{delta} > {total_cap} "
                f"(env WATCHLIST_MAX_ENABLED_TOTAL)"
            )

    # ── Internals ─────────────────────────────────────────────────

    @staticmethod
    def _to_dict(row: WatchlistEntry) -> dict:
        return {
            "id": row.id,
            "list_name": row.list_name,
            "symbol": row.symbol,
            "exchange": row.exchange,
            "enabled": row.enabled,
            "max_notional_usdt_override": row.max_notional_usdt_override,
            "tags": row.tags or [],
            "note": row.note or "",
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
