"""체크리스트 #11 Audit Foundation — 감사 이벤트 archive.

원칙:
  - 감사 이벤트는 **삭제하지 않는다**. archive 만 허용.
  - `archive_event()` 는 in-memory 이벤트 dict 에 `archived=True`,
    `archived_at`, `archived_by`, `archive_note` 필드를 채운다.
  - 이미 archived 인 이벤트를 다시 archive 해도 실패하지 않는다 (멱등).
  - 본 모듈은 어떤 row 도 삭제하는 API 를 제공하지 않는다.

DB schema 관련:
  - 현 `AuditEvent` 테이블 (app/db/models.py) 은 `archived` 컬럼이 없다.
  - 본 단계에서는 schema 변경 없이 in-memory 마킹으로 처리한다 (체크리스트 #11
    "[13단계: DB migration 판단] — 기본 방침: 새 컬럼 추가 지양").
  - 추후 DB 컬럼 추가가 필요해지면 별도 migration 항목으로 진행.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from .audit_log import AuditLog


class AuditEventNotFoundError(LookupError):
    """archive 대상 이벤트를 찾지 못함."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _find_event(audit: AuditLog, *, event_id: Optional[int] = None,
                ts: Optional[str] = None) -> dict:
    """이벤트를 식별. id (index) 또는 ts string 으로.

    - `event_id` 는 in-memory `audit.events` 의 0-base 인덱스.
    - `ts` 는 record() 시점에 저장된 ISO timestamp 문자열.
    """
    if event_id is None and ts is None:
        raise ValueError("event_id 또는 ts 중 하나는 필수")
    if event_id is not None:
        if not (0 <= event_id < len(audit.events)):
            raise AuditEventNotFoundError(
                f"audit event id={event_id} 가 존재하지 않습니다 "
                f"(in-memory size={len(audit.events)})"
            )
        return audit.events[event_id]
    # ts 검색
    for ev in audit.events:
        if ev.get("ts") == ts:
            return ev
    raise AuditEventNotFoundError(f"audit event ts={ts!r} 를 찾을 수 없습니다")


def archive_event(
    audit: AuditLog,
    *,
    event_id: Optional[int] = None,
    ts: Optional[str] = None,
    archived_by: str = "operator",
    archive_note: str = "",
) -> dict:
    """이벤트를 archive (삭제 아님).

    Returns
    -------
    dict
        archive 가 적용된 이벤트 dict (in-place 수정된 동일 객체).

    Raises
    ------
    AuditEventNotFoundError
        대상 이벤트가 없을 때.
    ValueError
        event_id, ts 모두 None 일 때.
    """
    ev = _find_event(audit, event_id=event_id, ts=ts)

    # 이미 archived → 멱등 처리 (필드 갱신 없이 그대로 반환)
    if ev.get("archived") is True:
        return ev

    ev["archived"] = True
    ev["archived_at"] = _utc_now().isoformat()
    ev["archived_by"] = archived_by
    ev["archive_note"] = archive_note
    return ev


def is_archived(ev: dict) -> bool:
    """이벤트 dict 가 archive 상태인지."""
    return bool(ev.get("archived", False))


def list_active(audit: AuditLog) -> list[dict]:
    """archived 되지 않은 이벤트만 반환 (기본 API 응답용)."""
    return [e for e in audit.events if not is_archived(e)]


__all__ = [
    "AuditEventNotFoundError",
    "archive_event",
    "is_archived",
    "list_active",
]
