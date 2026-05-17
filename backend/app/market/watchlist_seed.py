"""Watchlist seed import — 체크리스트 #14.

JSON 템플릿(예: ``config/watchlists/default.json``)을 읽어 DB 에 적재한다.
중복 (list_name, symbol, exchange) 항목은 **건너뛴다** — 멱등(idempotent).

사용:
    python -m app.market.watchlist_seed config/watchlists/default.json
    python -m app.market.watchlist_seed config/watchlists/majors.json --update-tags

원칙:
  - 거래소 API 호출 없음. 파일 → DB 단방향.
  - Watchlist 는 주문 허용 목록이 아님. seed 후에도 RiskManager/OrderGuard 통과 필요.
  - JSON 의 _note 필드는 무시.
"""
from __future__ import annotations
import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from sqlalchemy.orm import Session

from app.market.watchlist import (
    WatchlistService,
    WatchlistDuplicateError,
    WatchlistValidationError,
    WatchlistLimitError,
    _normalize_symbol, _normalize_exchange, _normalize_list_name,
)
from app.db.models import WatchlistEntry


@dataclass(frozen=True)
class SeedReport:
    list_name: str
    added: int
    skipped_duplicate: int
    skipped_invalid: int
    skipped_limit: int
    updated: int

    def as_dict(self) -> dict:
        return {
            "list_name": self.list_name,
            "added": self.added,
            "skipped_duplicate": self.skipped_duplicate,
            "skipped_invalid": self.skipped_invalid,
            "skipped_limit": self.skipped_limit,
            "updated": self.updated,
        }


def load_seed_file(path: Path) -> tuple[str, list[dict]]:
    """JSON 파일에서 (list_name, entries) 를 추출."""
    data = json.loads(path.read_text(encoding="utf-8"))
    list_name = data.get("list_name") or "default"
    entries   = data.get("entries", [])
    if not isinstance(entries, list):
        raise ValueError(f"'entries' must be a list in {path}")
    return list_name, entries


def import_entries(
    session: Session,
    list_name: str,
    entries: Iterable[dict],
    *,
    update_tags: bool = False,
) -> SeedReport:
    """엔트리 리스트를 WatchlistService 로 적재.

    중복 항목은 기본 건너뛴다. ``update_tags=True`` 면 기존 행의 tags/note 만 갱신.
    """
    svc = WatchlistService(session)
    added = skipped_dup = skipped_invalid = skipped_limit = updated = 0

    for raw in entries:
        symbol = raw.get("symbol", "")
        exchange = raw.get("exchange", "upbit")
        enabled = bool(raw.get("enabled", True))
        tags = list(raw.get("tags", []) or [])
        note = str(raw.get("note", "") or "")

        try:
            svc.add(
                symbol=symbol, exchange=exchange, list_name=list_name,
                enabled=enabled, tags=tags, note=note,
            )
            added += 1
        except WatchlistDuplicateError:
            skipped_dup += 1
            if update_tags:
                # 기존 행 찾아 tags/note 만 갱신.
                row = (
                    session.query(WatchlistEntry)
                    .filter_by(
                        list_name=_normalize_list_name(list_name),
                        symbol=_normalize_symbol(symbol),
                        exchange=_normalize_exchange(exchange),
                    )
                    .one_or_none()
                )
                if row is not None:
                    row.tags = tags
                    row.note = note
                    session.commit()
                    updated += 1
        except WatchlistValidationError:
            skipped_invalid += 1
        except WatchlistLimitError:
            skipped_limit += 1

    return SeedReport(
        list_name=list_name,
        added=added,
        skipped_duplicate=skipped_dup,
        skipped_invalid=skipped_invalid,
        skipped_limit=skipped_limit,
        updated=updated,
    )


def import_file(
    session: Session,
    path: Path,
    *,
    update_tags: bool = False,
) -> SeedReport:
    list_name, entries = load_seed_file(path)
    return import_entries(session, list_name, entries, update_tags=update_tags)


def _cli() -> int:
    p = argparse.ArgumentParser(description="Watchlist seed import (#14).")
    p.add_argument("path", help="config/watchlists/*.json 경로")
    p.add_argument("--update-tags", action="store_true",
                   help="중복 항목의 tags/note 를 파일 값으로 덮어쓴다")
    args = p.parse_args()

    path = Path(args.path)
    if not path.exists():
        print(f"file not found: {path}", file=sys.stderr)
        return 2

    from app.db.session import get_session_factory, create_all_tables
    create_all_tables()
    Sf = get_session_factory()
    with Sf() as s:
        report = import_file(s, path, update_tags=args.update_tags)

    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
