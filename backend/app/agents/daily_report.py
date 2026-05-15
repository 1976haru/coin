"""DailyReportAgent — 체크리스트 #42 Daily Report Agent.

AuditLog 의 이벤트를 집계해 운영자/감사용 일일 리포트 생성.
결정론 — LLM 사용 안 함.

집계 항목:
  - order_summary: {submitted, filled(paper), rejected, blocked, pending, shadow}
  - agent_summary: {by_role, total, vetos, by_action}
  - key_events: kill_switch / BLOCKED orders / Risk veto 등 주요 이벤트
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from typing import Any

from .base import AgentCapability


# ── 리포트 타입 ──────────────────────────────────────────────────

@dataclass(frozen=True)
class OrderSummary:
    submitted: int = 0
    filled_paper: int = 0
    rejected: int = 0
    blocked: int = 0
    pending_approval: int = 0
    shadow_logged: int = 0
    intents: int = 0


@dataclass(frozen=True)
class AgentSummary:
    total_decisions: int = 0
    by_role: dict[str, int] = field(default_factory=dict)
    by_action: dict[str, int] = field(default_factory=dict)
    veto_count: int = 0
    watch_only_count: int = 0


@dataclass(frozen=True)
class DailyReport:
    since: datetime
    until: datetime
    total_events: int
    order_summary: OrderSummary
    agent_summary: AgentSummary
    key_events: tuple[dict, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "since": self.since.isoformat(),
            "until": self.until.isoformat(),
            "total_events": self.total_events,
            "order_summary": asdict(self.order_summary),
            "agent_summary": asdict(self.agent_summary),
            "key_events": list(self.key_events),
        }


# ── 이벤트 카테고리 매핑 ────────────────────────────────────────

_ORDER_EVENT_TO_FIELD = {
    "ORDER_INTENT":              "intents",
    "ORDER_SUBMITTED":           "submitted",
    "PAPER_ORDER_FILLED":        "filled_paper",
    "ORDER_REJECTED_BY_RISK":    "rejected",
    "ORDER_REJECTED_BY_IDEMPOTENCY": "rejected",
    "ORDER_REJECTED":            "rejected",
    "ORDER_BLOCKED_BY_PERMISSION": "blocked",
    "LIVE_EXECUTOR_NOT_WIRED":   "blocked",
    "ORDER_QUEUED_FOR_APPROVAL": "pending_approval",
    "SHADOW_SIGNAL_LOGGED":      "shadow_logged",
}

_KEY_EVENT_TYPES = frozenset({
    "KILL_SWITCH_ACTIVATED",
    "KILL_SWITCH_DEACTIVATED",
    "ORDER_BLOCKED_BY_PERMISSION",
    "ORDER_REJECTED_BY_RISK",
    "LIVE_EXECUTOR_NOT_WIRED",
    "ORDER_DENIED",
})


# ── DailyReportAgent ────────────────────────────────────────────

class DailyReportAgent:
    """AuditLog 이벤트 집계 → DailyReport.

    호출 방식:
      report_agent.generate_report(audit, since=..., until=...)  # 직접
      report_agent.decide(_, {"audit_log": audit, "since": ...})  # AgentBase contract
    """

    capability = AgentCapability(
        name="daily_report",
        role="daily_report",
        description="AuditLog 이벤트 집계 — 일일 주문/Agent 결정 리포트 + 주요 이벤트.",
        has_veto_power=False,
        is_deterministic=True,
        requires_llm=False,
        inputs=("audit_log", "since", "until"),
    )

    # ── AgentBase contract ────────────────────────────────────────

    def decide(self, input_signal: dict, context: dict | None = None) -> Any:
        from .orchestrator import AgentDecision
        ctx = context or {}
        audit = ctx.get("audit_log")
        since = ctx.get("since")
        until = ctx.get("until")
        if audit is None:
            return AgentDecision(
                "HOLD", 0.0,
                "DailyReportAgent: audit_log 미제공",
                explain_text="audit_log 미제공 — 리포트 생성 불가",
            )
        report = self.generate_report(audit, since=since, until=until)
        return AgentDecision(
            "HOLD", 0.0,
            "DailyReportAgent: 리포트 생성",
            explain_text=self.render_text(report, format="markdown"),
        )

    # ── 보고서 생성 ───────────────────────────────────────────────

    def generate_report(
        self,
        audit: Any,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> DailyReport:
        """AuditLog → DailyReport.

        since/until 미지정 시: 오늘 자정 ~ 현재.
        """
        now = datetime.now(timezone.utc)
        until = until or now
        since = since or self._start_of_today_utc(now)

        events = self._filter_events(audit, since, until)
        order_summary = self._aggregate_orders(events)
        agent_summary = self._aggregate_agents(events)
        key_events = self._collect_key_events(events)

        return DailyReport(
            since=since,
            until=until,
            total_events=len(events),
            order_summary=order_summary,
            agent_summary=agent_summary,
            key_events=tuple(key_events),
        )

    def render_text(
        self,
        report: DailyReport,
        *,
        format: str = "markdown",
    ) -> str:
        os_ = report.order_summary
        ag = report.agent_summary

        if format == "markdown":
            lines = [
                "## 일일 거래 리포트",
                f"- **기간**: `{report.since.isoformat()}` ~ `{report.until.isoformat()}`",
                f"- **총 이벤트**: {report.total_events}",
                "",
                "### 주문 요약",
                f"- 의도(INTENT): {os_.intents}",
                f"- 제출(SUBMITTED): {os_.submitted}",
                f"- 체결(PAPER FILLED): {os_.filled_paper}",
                f"- 거부(REJECTED): {os_.rejected}",
                f"- 차단(BLOCKED): {os_.blocked}",
                f"- 승인 대기: {os_.pending_approval}",
                f"- Shadow 로깅: {os_.shadow_logged}",
                "",
                "### Agent 결정 요약",
                f"- 총 결정: {ag.total_decisions}",
                f"- 거부권 행사: {ag.veto_count}",
                f"- WATCH_ONLY: {ag.watch_only_count}",
            ]
            if ag.by_role:
                lines.append("- **역할별**:")
                for role, n in sorted(ag.by_role.items()):
                    lines.append(f"  - {role}: {n}")
            if ag.by_action:
                lines.append("- **액션별**:")
                for act, n in sorted(ag.by_action.items()):
                    lines.append(f"  - {act}: {n}")
            if report.key_events:
                lines.append("")
                lines.append(f"### 주요 이벤트 ({len(report.key_events)})")
                for ev in report.key_events[:10]:
                    lines.append(f"- `{ev['ts']}` **{ev['event_type']}**")
            return "\n".join(lines)

        # plain
        lines = [
            "=== 일일 거래 리포트 ===",
            f"기간: {report.since.isoformat()} ~ {report.until.isoformat()}",
            f"총 이벤트: {report.total_events}",
            "",
            "[주문 요약]",
            f"  의도={os_.intents} 제출={os_.submitted} 체결={os_.filled_paper}",
            f"  거부={os_.rejected} 차단={os_.blocked} 승인대기={os_.pending_approval}",
            f"  Shadow={os_.shadow_logged}",
            "",
            "[Agent 결정 요약]",
            f"  총={ag.total_decisions} 거부권={ag.veto_count} WATCH_ONLY={ag.watch_only_count}",
        ]
        if ag.by_role:
            lines.append("  역할별: " +
                          ", ".join(f"{k}={v}" for k, v in sorted(ag.by_role.items())))
        if ag.by_action:
            lines.append("  액션별: " +
                          ", ".join(f"{k}={v}" for k, v in sorted(ag.by_action.items())))
        if report.key_events:
            lines.append("")
            lines.append(f"[주요 이벤트 ({len(report.key_events)})]")
            for ev in report.key_events[:10]:
                lines.append(f"  {ev['ts']} {ev['event_type']}")
        return "\n".join(lines)

    # ── 내부 ──────────────────────────────────────────────────────

    @staticmethod
    def _start_of_today_utc(now: datetime) -> datetime:
        return now.replace(hour=0, minute=0, second=0, microsecond=0)

    @staticmethod
    def _parse_ts(ts: Any) -> datetime | None:
        if isinstance(ts, datetime):
            return ts
        if isinstance(ts, str):
            try:
                return datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                return None
        return None

    def _filter_events(
        self, audit: Any, since: datetime, until: datetime,
    ) -> list[dict]:
        events = list(getattr(audit, "events", []) or [])
        out: list[dict] = []
        for ev in events:
            t = self._parse_ts(ev.get("ts"))
            if t is None:
                # ts 누락 — 보수적으로 포함
                out.append(ev)
                continue
            if since <= t <= until:
                out.append(ev)
        return out

    def _aggregate_orders(self, events: list[dict]) -> OrderSummary:
        counts = {f.name: 0 for f in OrderSummary.__dataclass_fields__.values()}
        for ev in events:
            field_name = _ORDER_EVENT_TO_FIELD.get(ev.get("event_type", ""))
            if field_name:
                counts[field_name] += 1
        return OrderSummary(**counts)

    def _aggregate_agents(self, events: list[dict]) -> AgentSummary:
        by_role: dict[str, int] = {}
        by_action: dict[str, int] = {}
        veto_count = 0
        watch_only = 0
        total = 0
        for ev in events:
            if ev.get("event_type") != "AGENT_DECISION":
                continue
            payload = ev.get("payload", {}) or {}
            role = payload.get("agent_role", "unknown")
            decision = payload.get("decision", {}) or {}
            action = decision.get("action", "unknown")
            by_role[role] = by_role.get(role, 0) + 1
            by_action[action] = by_action.get(action, 0) + 1
            if decision.get("risk_veto"):
                veto_count += 1
            if action == "WATCH_ONLY":
                watch_only += 1
            total += 1
        return AgentSummary(
            total_decisions=total,
            by_role=by_role,
            by_action=by_action,
            veto_count=veto_count,
            watch_only_count=watch_only,
        )

    @staticmethod
    def _collect_key_events(events: list[dict]) -> list[dict]:
        out: list[dict] = []
        for ev in events:
            if ev.get("event_type") in _KEY_EVENT_TYPES:
                out.append({
                    "ts": ev.get("ts", ""),
                    "event_type": ev.get("event_type", ""),
                })
        return out
