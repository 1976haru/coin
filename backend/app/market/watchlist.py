"""WatchlistService — 거래 대상 universe 관리. 체크리스트 #14.

순수 서비스 레이어. SQLAlchemy Session을 주입받고, ORM 모델을 dict로 직렬화한다.
주문 흐름과 분리되어 있어 RiskManager·OrderGateway를 우회하지 않는다.
"""
from __future__ import annotations
from typing import Sequence

from sqlalchemy import select, delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import WatchlistEntry


class WatchlistDuplicateError(ValueError):
    """동일 (list_name, symbol, exchange) 조합이 이미 존재."""


class WatchlistNotFoundError(LookupError):
    """id로 항목을 찾을 수 없음."""


class WatchlistService:
    """거래 universe CRUD.

    list_name 기본값 "default". 여러 목록(예: "kimp_pairs", "majors")을 동시에 운영 가능.
    """

    def __init__(self, session: Session):
        self.s = session

    # ── Read ──────────────────────────────────────────────────────

    def list_entries(
        self,
        list_name: str | None = None,
        exchange: str | None = None,
        enabled_only: bool = False,
    ) -> list[dict]:
        stmt = select(WatchlistEntry)
        if list_name is not None:
            stmt = stmt.where(WatchlistEntry.list_name == list_name)
        if exchange is not None:
            stmt = stmt.where(WatchlistEntry.exchange == exchange)
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
            stmt = stmt.where(WatchlistEntry.list_name == list_name)
        if enabled_only:
            stmt = stmt.where(WatchlistEntry.enabled.is_(True))
        return len(self.s.execute(stmt).scalars().all())

    def list_names(self) -> list[str]:
        stmt = select(WatchlistEntry.list_name).distinct().order_by(WatchlistEntry.list_name)
        return [name for (name,) in self.s.execute(stmt).all()]

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
        entry = WatchlistEntry(
            list_name=list_name,
            symbol=symbol,
            exchange=exchange,
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
                f"({list_name}, {symbol}, {exchange}) already exists"
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
        row.enabled = enabled
        self.s.commit()
        self.s.refresh(row)
        return self._to_dict(row)

    def remove_by_list(self, list_name: str) -> int:
        """list_name 전체 제거. 반환값: 삭제된 행 수."""
        stmt = delete(WatchlistEntry).where(WatchlistEntry.list_name == list_name)
        result = self.s.execute(stmt)
        self.s.commit()
        return int(result.rowcount or 0)

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
