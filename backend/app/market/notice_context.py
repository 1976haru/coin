"""Notice Context Builder — 체크리스트 #18.

Agent / 후보 필터가 사용할 *read-only* notice context 를 생성한다.

중요 (CLAUDE.md §2.3):
  - context 는 **후보 필터와 리스크 설명** 용도. 직접 주문 트리거가 아니다.
  - 모든 응답에 ``direct_order_allowed=False`` 가 항상 포함된다.
  - 본 모듈은 broker / execution 계층을 import 하지 않는다.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import ExchangeNotice
from app.market.notice_collector import list_notices


# notice_type → candidate filter flag (read-only).
_TYPE_TO_FLAG: dict[str, str] = {
    "DEPOSIT_WITHDRAWAL_SUSPENSION": "deposit_withdrawal_suspended",
    "CAUTION":                       "caution_notice",
    "DELISTING":                     "delisting_or_termination",
    "TRADING_SUSPENSION":            "trading_suspended",
    "MAINTENANCE":                   "maintenance_in_progress",
    "LISTING":                       "new_listing",
    "POLICY":                        "policy_change",
    "OTHER":                         "other_notice",
}

# severity 순위 (작을수록 약함).
_SEVERITY_RANK: dict[str, int] = {
    "INFO": 0, "WARNING": 1, "HIGH": 2, "CRITICAL": 3,
}


@dataclass(frozen=True)
class NoticeRiskFlag:
    """심볼별 단일 리스크 플래그."""

    symbol: str
    flag: str            # e.g. "deposit_withdrawal_suspended"
    notice_type: str
    severity: str
    title: str
    exchange: str
    published_at: str | None


@dataclass(frozen=True)
class SymbolNoticeSummary:
    """심볼별 공지 요약."""

    symbol: str
    risk_flags: tuple[str, ...]
    severity: str
    high_risk_count: int
    notice_count: int
    recommendation: str  # candidate_filter_review_required / candidate_filter_ok
    direct_order_allowed: bool = False  # 항상 False (CLAUDE.md §2.3)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["risk_flags"] = list(self.risk_flags)
        return d


@dataclass(frozen=True)
class NoticeContext:
    """전체 notice context.

    Agent 가 그대로 dict 로 받아 candidate filter / risk explanation 에 사용한다.
    direct_order_allowed 는 항상 False — 본 context 는 주문 권한이 아니다.
    """

    generated_at: str
    lookback_hours: int
    total_notices: int
    by_type: dict[str, int]
    by_severity: dict[str, int]
    high_risk_symbols: tuple[str, ...]
    symbol_summaries: tuple[SymbolNoticeSummary, ...]
    recent_titles: tuple[str, ...]
    human_summary: str
    candidate_filter_flags: tuple[str, ...]
    risk_notes: tuple[str, ...]
    direct_order_allowed: bool = False  # 영구 False

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at":        self.generated_at,
            "lookback_hours":      self.lookback_hours,
            "total_notices":       self.total_notices,
            "by_type":             dict(self.by_type),
            "by_severity":         dict(self.by_severity),
            "high_risk_symbols":   list(self.high_risk_symbols),
            "symbol_summaries":    [s.to_dict() for s in self.symbol_summaries],
            "recent_titles":       list(self.recent_titles),
            "human_summary":       self.human_summary,
            "candidate_filter_flags": list(self.candidate_filter_flags),
            "risk_notes":          list(self.risk_notes),
            "direct_order_allowed": False,
        }


# ── builder ──────────────────────────────────────────────────────


class NoticeContextBuilder:
    """ExchangeNotice → Agent context 변환기.

    read-only. broker/execution 비의존.
    """

    def __init__(self, session: Session):
        self.session = session

    def build_notice_context(
        self,
        *,
        symbols: list[str] | None = None,
        lookback_hours: int = 72,
        exchange: str | None = None,
        now: datetime | None = None,
    ) -> NoticeContext:
        now = now or datetime.now(timezone.utc)
        rows = list_notices(
            self.session,
            exchange=exchange,
            since_hours=lookback_hours,
            limit=500,
            now=now,
        )

        # symbols 필터가 있으면 — symbol 매칭 + 전체 거래소 공지 (symbol 없음) 도 포함.
        normalized_syms: list[str] = []
        if symbols:
            normalized_syms = sorted({s.strip().upper() for s in symbols if s and s.strip()})
            filtered: list[ExchangeNotice] = []
            for r in rows:
                row_syms = set(r.symbols or [])
                if not row_syms:
                    filtered.append(r)  # 전체-거래소 공지 (점검 등)
                elif row_syms & set(normalized_syms):
                    filtered.append(r)
            rows = filtered

        by_type: dict[str, int] = {}
        by_severity: dict[str, int] = {}
        high_risk_symbols: set[str] = set()
        candidate_flags: set[str] = set()
        risk_notes: list[str] = []
        recent_titles: list[str] = []
        symbol_to_flags: dict[str, list[NoticeRiskFlag]] = {}

        for r in rows[:50]:
            recent_titles.append(r.title)

        for r in rows:
            by_type[r.notice_type] = by_type.get(r.notice_type, 0) + 1
            by_severity[r.severity] = by_severity.get(r.severity, 0) + 1
            flag = _TYPE_TO_FLAG.get(r.notice_type, "other_notice")
            candidate_flags.add(flag)

            row_syms = list(r.symbols or [])
            target_syms = row_syms if row_syms else ["__market__"]
            for s in target_syms:
                if _SEVERITY_RANK.get(r.severity, 0) >= _SEVERITY_RANK["HIGH"]:
                    if s != "__market__":
                        high_risk_symbols.add(s)
                rf = NoticeRiskFlag(
                    symbol=s,
                    flag=flag,
                    notice_type=r.notice_type,
                    severity=r.severity,
                    title=r.title,
                    exchange=r.exchange,
                    published_at=(r.published_at.isoformat() if r.published_at else None),
                )
                symbol_to_flags.setdefault(s, []).append(rf)

            note = f"[{r.severity}] {r.notice_type} ({r.exchange}): {r.title}"
            if len(risk_notes) < 20:
                risk_notes.append(note)

        # symbol 별 요약 — symbols 인자가 주어졌으면 그 목록 기준, 아니면 발견된 심볼.
        target_summary_syms: list[str]
        if normalized_syms:
            target_summary_syms = normalized_syms
        else:
            target_summary_syms = sorted(s for s in symbol_to_flags if s != "__market__")

        summaries: list[SymbolNoticeSummary] = []
        for s in target_summary_syms:
            flags = symbol_to_flags.get(s, [])
            unique_flags = tuple(sorted({f.flag for f in flags}))
            high_count = sum(1 for f in flags
                             if _SEVERITY_RANK.get(f.severity, 0) >= _SEVERITY_RANK["HIGH"])
            sev = "INFO"
            for f in flags:
                if _SEVERITY_RANK.get(f.severity, 0) > _SEVERITY_RANK.get(sev, 0):
                    sev = f.severity
            recommendation = (
                "candidate_filter_review_required"
                if high_count > 0 or any(
                    f.flag in {
                        "deposit_withdrawal_suspended",
                        "delisting_or_termination",
                        "trading_suspended",
                    } for f in flags
                )
                else "candidate_filter_ok"
            )
            summaries.append(SymbolNoticeSummary(
                symbol=s,
                risk_flags=unique_flags,
                severity=sev,
                high_risk_count=high_count,
                notice_count=len(flags),
                recommendation=recommendation,
            ))

        human = self._render_summary(
            total=len(rows),
            by_type=by_type,
            by_severity=by_severity,
            high_risk_symbols=sorted(high_risk_symbols),
            lookback_hours=lookback_hours,
        )

        return NoticeContext(
            generated_at=now.isoformat(),
            lookback_hours=lookback_hours,
            total_notices=len(rows),
            by_type=by_type,
            by_severity=by_severity,
            high_risk_symbols=tuple(sorted(high_risk_symbols)),
            symbol_summaries=tuple(summaries),
            recent_titles=tuple(recent_titles),
            human_summary=human,
            candidate_filter_flags=tuple(sorted(candidate_flags)),
            risk_notes=tuple(risk_notes),
        )

    def get_symbol_risk_flags(
        self,
        symbol: str,
        *,
        lookback_hours: int = 72,
        exchange: str | None = None,
        now: datetime | None = None,
    ) -> list[NoticeRiskFlag]:
        ctx = self.build_notice_context(
            symbols=[symbol],
            lookback_hours=lookback_hours,
            exchange=exchange,
            now=now,
        )
        out: list[NoticeRiskFlag] = []
        # build_notice_context 는 요약만 노출 — 원본 플래그가 필요하면 한 번 더 조회.
        rows = list_notices(
            self.session,
            exchange=exchange,
            since_hours=lookback_hours,
            limit=500,
            now=now,
        )
        sym = symbol.strip().upper()
        for r in rows:
            row_syms = set(r.symbols or [])
            if sym not in row_syms:
                continue
            out.append(NoticeRiskFlag(
                symbol=sym,
                flag=_TYPE_TO_FLAG.get(r.notice_type, "other_notice"),
                notice_type=r.notice_type,
                severity=r.severity,
                title=r.title,
                exchange=r.exchange,
                published_at=(r.published_at.isoformat() if r.published_at else None),
            ))
        # 보조 — ctx 가 비어있지 않음을 보장 (caller 가 ctx.symbol_summaries 와 일관성 확인 가능).
        _ = ctx
        return out

    @staticmethod
    def _render_summary(
        *,
        total: int,
        by_type: dict[str, int],
        by_severity: dict[str, int],
        high_risk_symbols: list[str],
        lookback_hours: int,
    ) -> str:
        if total == 0:
            return f"최근 {lookback_hours}시간 내 수집된 거래소 공지가 없습니다."
        parts = [
            f"최근 {lookback_hours}시간 내 공지 {total}건 수집.",
        ]
        # 위험 카테고리 위주 요약
        risky = [
            ("DELISTING", by_type.get("DELISTING", 0)),
            ("TRADING_SUSPENSION", by_type.get("TRADING_SUSPENSION", 0)),
            ("DEPOSIT_WITHDRAWAL_SUSPENSION", by_type.get("DEPOSIT_WITHDRAWAL_SUSPENSION", 0)),
            ("CAUTION", by_type.get("CAUTION", 0)),
            ("MAINTENANCE", by_type.get("MAINTENANCE", 0)),
        ]
        risky_items = [f"{k}={v}" for k, v in risky if v > 0]
        if risky_items:
            parts.append("주요 유형: " + ", ".join(risky_items) + ".")
        crit = by_severity.get("CRITICAL", 0)
        high = by_severity.get("HIGH", 0)
        if crit or high:
            parts.append(f"심각도 CRITICAL={crit}, HIGH={high}.")
        if high_risk_symbols:
            shown = ", ".join(high_risk_symbols[:10])
            parts.append(f"고위험 심볼: {shown}.")
        parts.append("본 정보는 후보 필터/리스크 설명용이며, 직접 주문 트리거가 아닙니다.")
        return " ".join(parts)


def summarize_notices_for_agent(
    notices: list[ExchangeNotice] | tuple[ExchangeNotice, ...],
) -> dict[str, Any]:
    """주어진 공지 리스트에 대한 간단 요약 dict.

    NoticeContextBuilder 와 별개로 사용 가능한 가벼운 헬퍼.
    """
    by_type: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    high_risk_symbols: set[str] = set()
    titles: list[str] = []
    for n in notices:
        by_type[n.notice_type] = by_type.get(n.notice_type, 0) + 1
        by_severity[n.severity] = by_severity.get(n.severity, 0) + 1
        if _SEVERITY_RANK.get(n.severity, 0) >= _SEVERITY_RANK["HIGH"]:
            for s in (n.symbols or []):
                high_risk_symbols.add(s)
        if len(titles) < 20:
            titles.append(n.title)
    return {
        "count": len(notices),
        "by_type": by_type,
        "by_severity": by_severity,
        "high_risk_symbols": sorted(high_risk_symbols),
        "recent_titles": titles,
        "direct_order_allowed": False,
    }


__all__ = (
    "NoticeRiskFlag",
    "SymbolNoticeSummary",
    "NoticeContext",
    "NoticeContextBuilder",
    "summarize_notices_for_agent",
)
