"""ApprovalQueue — TTL 만료 자동 처리. LIVE_MANUAL_APPROVAL 모드 핵심.

체크리스트 #55 Manual Approval, #58 AI Assist (source 트래킹).
이전 위치: app/risk/approval_queue.py
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta, timezone
from uuid import uuid4


@dataclass
class ApprovalItem:
    id: str
    order: dict
    reason: str
    created_at: str
    expires_at: str
    status: str = "PENDING"    # PENDING | APPROVED | REJECTED | EXPIRED
    source: str = "system"     # system | strategy | ai | manual (#58)
    agent_explain: str = ""    # AgentOrchestrator explain_text (AI 출처일 때 채움)

    def is_expired(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        exp = datetime.fromisoformat(self.expires_at)
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return now > exp


class ApprovalQueue:
    def __init__(self, default_ttl_seconds: int = 300):
        self.default_ttl_seconds = default_ttl_seconds
        self._items: dict[str, ApprovalItem] = {}

    def add(
        self,
        order: dict,
        reason: str,
        ttl_seconds: int | None = None,
        *,
        source: str = "system",
        agent_explain: str = "",
    ) -> ApprovalItem:
        now = datetime.now(timezone.utc)
        ttl = ttl_seconds or self.default_ttl_seconds
        item = ApprovalItem(
            id=str(uuid4()),
            order=order,
            reason=reason,
            created_at=now.isoformat(),
            expires_at=(now + timedelta(seconds=ttl)).isoformat(),
            source=source,
            agent_explain=agent_explain,
        )
        self._items[item.id] = item
        return item

    def decide(self, item_id: str, approved: bool) -> ApprovalItem:
        if item_id not in self._items:
            raise KeyError(item_id)
        item = self._items[item_id]
        now = datetime.now(timezone.utc)
        if item.is_expired(now):
            item.status = "EXPIRED"
            return item
        item.status = "APPROVED" if approved else "REJECTED"
        return item

    def list(self) -> list[dict]:
        now = datetime.now(timezone.utc)
        for item in self._items.values():
            if item.status == "PENDING" and item.is_expired(now):
                item.status = "EXPIRED"
        return [asdict(x) for x in self._items.values()]

    def pending(self) -> list[ApprovalItem]:
        now = datetime.now(timezone.utc)
        return [
            x for x in self._items.values()
            if x.status == "PENDING" and not x.is_expired(now)
        ]

    def count_pending(self) -> int:
        return len(self.pending())

    def pending_by_source(self, source: str) -> list[ApprovalItem]:
        """source 별 pending 필터 — #58 AI Assist 모니터링용."""
        return [x for x in self.pending() if x.source == source]
