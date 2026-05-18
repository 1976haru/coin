"""Market Observer Agent — 체크리스트 #38 Market Observer.

시장지수·거래대금·급등락·섹터 흐름·변동성·freshness/data quality·notice·theme 을
관찰하여 *장중 시장 환경* 을 JSON structured output 으로 요약하는 Agent.

본 모듈은 #37 6-role Agent Architecture 의 ``OBSERVER`` 역할 specialization 이다.

원칙 (CLAUDE.md §2.3 / §2.4 / §3.1):
  - 매수/매도/진입/청산 *결론을 내리지 않는다*.
  - broker / adapter / OrderGateway / MockBroker / PaperBroker 를 *호출하지 않는다*.
  - place_order / cancel_order / get_balance / submit_order / withdraw / deposit
    / set_leverage / set_margin 를 *호출하지 않는다*.
  - 외부 API / 거래소 / 뉴스 / 트렌드 endpoint 를 직접 호출하지 *않는다*.
  - 데이터 수집을 직접 수행하지 *않는다* — 입력으로 받은 context 만 관찰한다.
  - output 에 ``direct_order_allowed=False`` 와 ``broker_call_allowed=False`` 가
    항상 포함된다 (영구 False).
  - output 에 BUY / SELL / ENTER / EXIT 를 *실행 action* 으로 반환하지 않는다.
  - executable_order / order_request / broker_payload 등 주문 페이로드를 *생성하지
    않는다*.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, ClassVar, Mapping, Sequence

from app.agents.base import (
    AgentArchitectureRole,
    AgentCard,
    AgentDecision,
    AgentFinding,
    AgentInput,
    AgentOutput,
    AgentPermission,
    AgentSafetyPolicy,
    StructuredAgentBase,
)


# ── 상수 라벨 ────────────────────────────────────────────────────


class RiskTone:
    """Market breadth 기반 시장 분위기 라벨. *주문 명령이 아님*."""

    RISK_ON = "RISK_ON"
    RISK_OFF = "RISK_OFF"
    MIXED = "MIXED"
    UNKNOWN = "UNKNOWN"


class SectorTone:
    """섹터 흐름 라벨."""

    STRONG = "STRONG"
    WEAK = "WEAK"
    MIXED = "MIXED"
    UNKNOWN = "UNKNOWN"


class VolatilityTone:
    """변동성 regime 라벨."""

    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    NORMAL = "NORMAL"
    UNKNOWN = "UNKNOWN"


# ── 내부 헬퍼 ────────────────────────────────────────────────────


def _to_float(value: Any) -> float | None:
    """안전 변환. None 또는 비숫자는 None."""
    if value is None:
        return None
    try:
        if isinstance(value, Decimal):
            return float(value)
        return float(value)
    except (TypeError, ValueError):
        return None


def _avg(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _median(values: Sequence[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2.0


def _extract_tickers(
    market_context: Mapping[str, Any] | None,
) -> list[Mapping[str, Any]]:
    """market_context 에서 ticker 리스트 평탄 추출. 없으면 빈 리스트."""
    if not market_context:
        return []
    tickers = market_context.get("tickers")
    if isinstance(tickers, Sequence) and not isinstance(tickers, str):
        return [t for t in tickers if isinstance(t, Mapping)]
    return []


# ── 데이터 구조 ──────────────────────────────────────────────────


@dataclass(frozen=True)
class MarketBreadthSnapshot:
    """시장 폭 (breadth) 요약."""

    total_symbols: int
    advancing_count: int
    declining_count: int
    unchanged_count: int
    advance_decline_ratio: float | None
    avg_change_pct: float | None
    median_change_pct: float | None
    risk_tone: str
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class VolumeFlowSummary:
    """거래대금/거래량 흐름 요약."""

    total_volume: float
    avg_volume_per_symbol: float | None
    top_volume_symbols: tuple[str, ...]
    surge_count: int
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class TopMover:
    """급등락 자산."""

    symbol: str
    change_pct: float
    volume: float | None = None
    direction: str = "UP"  # "UP" / "DOWN"


@dataclass(frozen=True)
class SectorFlow:
    """섹터/테마 흐름 요약."""

    sector: str
    symbols: tuple[str, ...]
    avg_change_pct: float | None
    total_volume: float
    theme_score: float | None
    tone: str
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class VolatilityRegimeSummary:
    """변동성 regime."""

    avg_volatility: float | None
    high_volatility_symbols: tuple[str, ...]
    volatility_tone: str
    transition_risk: bool
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class DataHealthSummary:
    """freshness + data quality 요약."""

    freshness_ok: bool | None
    stale_symbols: tuple[str, ...]
    data_quality_grade: str | None
    quality_excluded_count: int
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class MarketObserverOutput:
    """Market Observer 의 JSON structured output.

    *direct_order_allowed* 와 *broker_call_allowed* 가 영구 False 임을 명시한다.
    BUY/SELL/ENTER/EXIT 실행 action 을 포함하지 않는다.
    """

    role: str
    version: str
    generated_at: datetime
    summary: str
    has_data: bool
    market_breadth: MarketBreadthSnapshot | None
    volume_flow: VolumeFlowSummary | None
    top_movers: tuple[TopMover, ...]
    sector_flows: tuple[SectorFlow, ...]
    volatility_regime: VolatilityRegimeSummary | None
    data_health: DataHealthSummary | None
    notice_observation: Mapping[str, Any] | None
    theme_observation: Mapping[str, Any] | None
    kimp_context: Mapping[str, Any] | None
    funding_context: Mapping[str, Any] | None
    findings: tuple[AgentFinding, ...]
    direct_order_allowed: bool = False   # 영구 False
    broker_call_allowed: bool = False    # 영구 False
    used_for_order: bool = False         # 영구 False

    def to_dict(self) -> dict:
        return {
            "kind": "market_observer_output",
            "role": self.role,
            "version": self.version,
            "generated_at": self.generated_at.isoformat(),
            "summary": self.summary,
            "has_data": self.has_data,
            "market_breadth": (
                None if self.market_breadth is None
                else _market_breadth_to_dict(self.market_breadth)
            ),
            "volume_flow": (
                None if self.volume_flow is None
                else _volume_flow_to_dict(self.volume_flow)
            ),
            "top_movers": [_top_mover_to_dict(m) for m in self.top_movers],
            "sector_flows": [_sector_flow_to_dict(s) for s in self.sector_flows],
            "volatility_regime": (
                None if self.volatility_regime is None
                else _volatility_regime_to_dict(self.volatility_regime)
            ),
            "data_health": (
                None if self.data_health is None
                else _data_health_to_dict(self.data_health)
            ),
            "notice_observation": (
                None if self.notice_observation is None
                else dict(self.notice_observation)
            ),
            "theme_observation": (
                None if self.theme_observation is None
                else dict(self.theme_observation)
            ),
            "kimp_context": (
                None if self.kimp_context is None
                else dict(self.kimp_context)
            ),
            "funding_context": (
                None if self.funding_context is None
                else dict(self.funding_context)
            ),
            "findings": [f.to_dict() for f in self.findings],
            "direct_order_allowed": self.direct_order_allowed,
            "broker_call_allowed": self.broker_call_allowed,
            "used_for_order": self.used_for_order,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str, sort_keys=True)


def _market_breadth_to_dict(b: MarketBreadthSnapshot) -> dict:
    return {
        "total_symbols": b.total_symbols,
        "advancing_count": b.advancing_count,
        "declining_count": b.declining_count,
        "unchanged_count": b.unchanged_count,
        "advance_decline_ratio": b.advance_decline_ratio,
        "avg_change_pct": b.avg_change_pct,
        "median_change_pct": b.median_change_pct,
        "risk_tone": b.risk_tone,
        "notes": list(b.notes),
    }


def _volume_flow_to_dict(v: VolumeFlowSummary) -> dict:
    return {
        "total_volume": v.total_volume,
        "avg_volume_per_symbol": v.avg_volume_per_symbol,
        "top_volume_symbols": list(v.top_volume_symbols),
        "surge_count": v.surge_count,
        "notes": list(v.notes),
    }


def _top_mover_to_dict(m: TopMover) -> dict:
    return {
        "symbol": m.symbol,
        "change_pct": m.change_pct,
        "volume": m.volume,
        "direction": m.direction,
    }


def _sector_flow_to_dict(s: SectorFlow) -> dict:
    return {
        "sector": s.sector,
        "symbols": list(s.symbols),
        "avg_change_pct": s.avg_change_pct,
        "total_volume": s.total_volume,
        "theme_score": s.theme_score,
        "tone": s.tone,
        "notes": list(s.notes),
    }


def _volatility_regime_to_dict(v: VolatilityRegimeSummary) -> dict:
    return {
        "avg_volatility": v.avg_volatility,
        "high_volatility_symbols": list(v.high_volatility_symbols),
        "volatility_tone": v.volatility_tone,
        "transition_risk": v.transition_risk,
        "notes": list(v.notes),
    }


def _data_health_to_dict(d: DataHealthSummary) -> dict:
    return {
        "freshness_ok": d.freshness_ok,
        "stale_symbols": list(d.stale_symbols),
        "data_quality_grade": d.data_quality_grade,
        "quality_excluded_count": d.quality_excluded_count,
        "notes": list(d.notes),
    }


# ── 요약 함수 (helpers) ──────────────────────────────────────────


def summarize_market_breadth(
    market_context: Mapping[str, Any] | None,
    *,
    risk_off_decline_ratio: float = 0.6,
    risk_on_advance_ratio: float = 0.6,
) -> MarketBreadthSnapshot:
    """시장 폭 + risk_tone 요약.

    데이터 부족 시 ``UNKNOWN`` tone 반환. 주문 action 을 반환하지 않는다.
    """
    tickers = _extract_tickers(market_context)
    notes: list[str] = []
    if not tickers:
        return MarketBreadthSnapshot(
            total_symbols=0,
            advancing_count=0,
            declining_count=0,
            unchanged_count=0,
            advance_decline_ratio=None,
            avg_change_pct=None,
            median_change_pct=None,
            risk_tone=RiskTone.UNKNOWN,
            notes=("no tickers in market_context",),
        )
    changes: list[float] = []
    advancing = 0
    declining = 0
    unchanged = 0
    for t in tickers:
        cp = _to_float(t.get("change_pct"))
        if cp is None:
            unchanged += 1
            continue
        changes.append(cp)
        if cp > 0:
            advancing += 1
        elif cp < 0:
            declining += 1
        else:
            unchanged += 1
    total = len(tickers)
    ratio: float | None
    if declining > 0:
        ratio = advancing / declining
    else:
        ratio = float(advancing) if advancing else None
    avg_pct = _avg(changes)
    med_pct = _median(changes)
    # tone 분류
    if total == 0 or (advancing + declining) == 0:
        tone = RiskTone.UNKNOWN
    else:
        adv_share = advancing / total
        dec_share = declining / total
        if dec_share >= risk_off_decline_ratio:
            tone = RiskTone.RISK_OFF
        elif adv_share >= risk_on_advance_ratio:
            tone = RiskTone.RISK_ON
        else:
            tone = RiskTone.MIXED
    return MarketBreadthSnapshot(
        total_symbols=total,
        advancing_count=advancing,
        declining_count=declining,
        unchanged_count=unchanged,
        advance_decline_ratio=ratio,
        avg_change_pct=avg_pct,
        median_change_pct=med_pct,
        risk_tone=tone,
        notes=tuple(notes),
    )


def summarize_volume_flow(
    market_context: Mapping[str, Any] | None,
    *,
    surge_threshold_ratio: float = 2.0,
    top_n: int = 5,
) -> VolumeFlowSummary:
    """거래대금/거래량 흐름 요약.

    surge_threshold_ratio: ``volume / avg_volume`` 이 이 이상이면 surge.
    """
    tickers = _extract_tickers(market_context)
    if not tickers:
        return VolumeFlowSummary(
            total_volume=0.0,
            avg_volume_per_symbol=None,
            top_volume_symbols=(),
            surge_count=0,
            notes=("no tickers in market_context",),
        )
    by_volume: list[tuple[str, float, float | None]] = []
    surges = 0
    for t in tickers:
        sym = str(t.get("symbol", "?"))
        vol = _to_float(t.get("volume"))
        avg = _to_float(t.get("avg_volume"))
        by_volume.append((sym, vol or 0.0, avg))
        if vol and avg and avg > 0 and (vol / avg) >= surge_threshold_ratio:
            surges += 1
    total = sum(v for _, v, _ in by_volume)
    by_volume.sort(key=lambda x: x[1], reverse=True)
    top_syms = tuple(s for s, _, _ in by_volume[:top_n] if s != "?")
    return VolumeFlowSummary(
        total_volume=total,
        avg_volume_per_symbol=(total / len(by_volume)) if by_volume else None,
        top_volume_symbols=top_syms,
        surge_count=surges,
    )


def detect_top_movers(
    market_context: Mapping[str, Any] | None,
    *,
    top_n: int = 5,
    abs_change_threshold_pct: float = 0.0,
) -> tuple[TopMover, ...]:
    """change_pct 상위/하위 top_n 자산 추출.

    ``abs_change_threshold_pct`` 이상인 자산만 후보. 주문 action 을 반환하지 않는다.
    """
    tickers = _extract_tickers(market_context)
    rows: list[TopMover] = []
    for t in tickers:
        sym = str(t.get("symbol", "?"))
        cp = _to_float(t.get("change_pct"))
        vol = _to_float(t.get("volume"))
        if cp is None or sym == "?":
            continue
        if abs(cp) < abs_change_threshold_pct:
            continue
        direction = "UP" if cp >= 0 else "DOWN"
        rows.append(TopMover(symbol=sym, change_pct=cp, volume=vol,
                              direction=direction))
    rows.sort(key=lambda m: abs(m.change_pct), reverse=True)
    return tuple(rows[:top_n])


def summarize_sector_flow(
    market_context: Mapping[str, Any] | None,
    theme_context: Mapping[str, Any] | None = None,
) -> tuple[SectorFlow, ...]:
    """섹터 / 테마 흐름 요약.

    우선 ``market_context["sector_map"]`` 사용 — 없으면 theme_context 의 related_symbols
    로 최소 요약. theme_score 가 있어도 *주문 신호로 사용하지 않는다*.
    """
    tickers = _extract_tickers(market_context)
    tk_by_symbol = {str(t.get("symbol", "?")): t for t in tickers}
    sector_map: Mapping[str, Sequence[str]] | None = None
    if market_context and isinstance(market_context.get("sector_map"), Mapping):
        sector_map = market_context["sector_map"]  # type: ignore[assignment]
    flows: list[SectorFlow] = []
    if sector_map:
        for sector, symbols in sector_map.items():
            syms_t = tuple(str(s) for s in (symbols or ()))
            changes: list[float] = []
            volume = 0.0
            for s in syms_t:
                t = tk_by_symbol.get(s)
                if not t:
                    continue
                cp = _to_float(t.get("change_pct"))
                vol = _to_float(t.get("volume"))
                if cp is not None:
                    changes.append(cp)
                if vol is not None:
                    volume += vol
            avg_change = _avg(changes)
            tone = _classify_sector_tone(avg_change)
            flows.append(SectorFlow(
                sector=str(sector),
                symbols=syms_t,
                avg_change_pct=avg_change,
                total_volume=volume,
                theme_score=None,
                tone=tone,
            ))
        return tuple(flows)
    # theme_context fallback
    if theme_context and isinstance(theme_context, Mapping):
        themes = theme_context.get("themes") or theme_context.get("active_themes")
        if isinstance(themes, Sequence):
            for theme in themes:
                if not isinstance(theme, Mapping):
                    continue
                name = str(theme.get("name") or theme.get("theme") or "theme")
                related = theme.get("related_symbols") or theme.get("symbols") or ()
                syms_t = tuple(str(s) for s in related)
                changes = []
                volume = 0.0
                for s in syms_t:
                    t = tk_by_symbol.get(s)
                    if not t:
                        continue
                    cp = _to_float(t.get("change_pct"))
                    vol = _to_float(t.get("volume"))
                    if cp is not None:
                        changes.append(cp)
                    if vol is not None:
                        volume += vol
                avg_change = _avg(changes)
                tone = _classify_sector_tone(avg_change)
                score = _to_float(theme.get("score"))
                notes_list: list[str] = []
                if score is not None and abs(score) >= 0.8:
                    notes_list.append("theme score elevated — observe only, not an order signal")
                flows.append(SectorFlow(
                    sector=name,
                    symbols=syms_t,
                    avg_change_pct=avg_change,
                    total_volume=volume,
                    theme_score=score,
                    tone=tone,
                    notes=tuple(notes_list),
                ))
    return tuple(flows)


def _classify_sector_tone(avg_change: float | None) -> str:
    if avg_change is None:
        return SectorTone.UNKNOWN
    if avg_change >= 1.0:
        return SectorTone.STRONG
    if avg_change <= -1.0:
        return SectorTone.WEAK
    return SectorTone.MIXED


def summarize_volatility_regime(
    market_context: Mapping[str, Any] | None,
    *,
    high_vol_threshold_pct: float = 3.0,
    low_vol_threshold_pct: float = 0.5,
) -> VolatilityRegimeSummary:
    """변동성 regime 요약. volatility_summary 우선, 없으면 change_pct dispersion 추정."""
    notes: list[str] = []
    if market_context and isinstance(
        market_context.get("volatility_summary"), Mapping,
    ):
        vs = market_context["volatility_summary"]
        avg_vol = _to_float(vs.get("avg_volatility"))
        high_syms = tuple(str(s) for s in (vs.get("high_volatility_symbols") or ()))
        tone = vs.get("tone") or vs.get("volatility_tone")
        if tone not in (
            VolatilityTone.HIGH_VOLATILITY, VolatilityTone.LOW_VOLATILITY,
            VolatilityTone.NORMAL, VolatilityTone.UNKNOWN,
        ):
            tone = VolatilityTone.UNKNOWN
        # transition_risk: 별도 freshness/breadth 기반 보강은 호출자가
        transition = bool(vs.get("transition_risk", False))
        return VolatilityRegimeSummary(
            avg_volatility=avg_vol,
            high_volatility_symbols=high_syms,
            volatility_tone=str(tone),
            transition_risk=transition,
        )
    tickers = _extract_tickers(market_context)
    if not tickers:
        return VolatilityRegimeSummary(
            avg_volatility=None,
            high_volatility_symbols=(),
            volatility_tone=VolatilityTone.UNKNOWN,
            transition_risk=False,
            notes=("no tickers in market_context",),
        )
    abs_changes: list[float] = []
    high_syms_list: list[str] = []
    sharp_down = 0
    for t in tickers:
        cp = _to_float(t.get("change_pct"))
        sym = str(t.get("symbol", "?"))
        if cp is None or sym == "?":
            continue
        a = abs(cp)
        abs_changes.append(a)
        if a >= high_vol_threshold_pct:
            high_syms_list.append(sym)
        if cp <= -high_vol_threshold_pct:
            sharp_down += 1
    avg_abs = _avg(abs_changes)
    if avg_abs is None:
        tone = VolatilityTone.UNKNOWN
    elif avg_abs >= high_vol_threshold_pct:
        tone = VolatilityTone.HIGH_VOLATILITY
    elif avg_abs <= low_vol_threshold_pct:
        tone = VolatilityTone.LOW_VOLATILITY
    else:
        tone = VolatilityTone.NORMAL
    transition = tone == VolatilityTone.HIGH_VOLATILITY and sharp_down >= max(
        1, len(abs_changes) // 4,
    )
    return VolatilityRegimeSummary(
        avg_volatility=avg_abs,
        high_volatility_symbols=tuple(high_syms_list),
        volatility_tone=tone,
        transition_risk=transition,
        notes=tuple(notes),
    )


def summarize_data_health(
    market_context: Mapping[str, Any] | None,
) -> DataHealthSummary:
    """freshness + data quality 요약."""
    if not market_context:
        return DataHealthSummary(
            freshness_ok=None,
            stale_symbols=(),
            data_quality_grade=None,
            quality_excluded_count=0,
            notes=("no market_context",),
        )
    fresh = market_context.get("freshness_state")
    freshness_ok: bool | None
    stale_syms: list[str] = []
    if isinstance(fresh, Mapping):
        freshness_ok = bool(fresh.get("ok", True))
        stale_syms = [
            str(s) for s in (fresh.get("stale_symbols") or ())
        ]
    elif isinstance(fresh, bool):
        freshness_ok = fresh
    else:
        freshness_ok = None
    grade = market_context.get("data_quality_grade")
    excluded = 0
    quality_obj = market_context.get("data_quality_summary")
    if isinstance(quality_obj, Mapping):
        excluded = int(quality_obj.get("exclude_count", 0))
        if grade is None:
            grade = quality_obj.get("grade")
    return DataHealthSummary(
        freshness_ok=freshness_ok,
        stale_symbols=tuple(stale_syms),
        data_quality_grade=str(grade) if grade else None,
        quality_excluded_count=excluded,
    )


# ── Agent 본체 ────────────────────────────────────────────────────


class MarketObserverAgent(StructuredAgentBase):
    """장중 시장 환경 관찰자.

    역할: 시장 폭·거래대금·top movers·섹터/테마·변동성·freshness/data quality·
    notice/theme/kimp/funding context 를 JSON structured output 으로 요약.

    *주문 결론을 만들지 않으며 broker / adapter / OrderGateway 를 호출하지 않는다*.
    """

    role: ClassVar[AgentArchitectureRole] = AgentArchitectureRole.OBSERVER
    card: ClassVar[AgentCard] = AgentCard(
        role=AgentArchitectureRole.OBSERVER,
        title="Market Observer Agent",
        description=(
            "시장지수·거래대금·급등락·섹터 흐름·변동성·freshness/data quality·"
            "notice·theme 을 관찰해 장중 시장 환경을 structured JSON 으로 요약. "
            "주문 결론은 만들지 않는다."
        ),
        inputs=(
            "market_context", "theme_context", "notice_context",
            "kimp_context", "funding_context",
        ),
        outputs=(
            "market_breadth", "volume_flow", "top_movers", "sector_flows",
            "volatility_regime", "data_health", "notice_observation",
            "theme_observation",
        ),
        forbidden_actions=(
            "execute_order", "invoke_broker", "invoke_order_gateway",
            "write_order_request", "place_order", "cancel_order",
            "get_balance", "fetch_external_api", "collect_market_data",
        ),
        allowed_permissions=frozenset((
            AgentPermission.READ_MARKET_DATA,
            AgentPermission.READ_FRESHNESS,
            AgentPermission.READ_DATA_QUALITY,
            AgentPermission.READ_NOTICES,
            AgentPermission.READ_THEMES,
            AgentPermission.READ_KIMP,
            AgentPermission.READ_FUNDING,
            AgentPermission.WRITE_FINDING,
        )),
    )
    safety: ClassVar[AgentSafetyPolicy] = AgentSafetyPolicy()

    def observe(self, input: AgentInput) -> MarketObserverOutput:
        """``MarketObserverOutput`` (rich structured) 반환."""
        payload = input.payload or {}
        market_ctx = payload.get("market_context")
        theme_ctx = payload.get("theme_context")
        notice_ctx = payload.get("notice_context")
        kimp_ctx = payload.get("kimp_context")
        funding_ctx = payload.get("funding_context")
        if not isinstance(market_ctx, Mapping):
            market_ctx = None
        if not isinstance(theme_ctx, Mapping):
            theme_ctx = None
        if not isinstance(notice_ctx, Mapping):
            notice_ctx = None
        if not isinstance(kimp_ctx, Mapping):
            kimp_ctx = None
        if not isinstance(funding_ctx, Mapping):
            funding_ctx = None

        # 데이터 부족 안전 경로
        tickers = _extract_tickers(market_ctx)
        has_data = bool(tickers) or bool(theme_ctx) or bool(notice_ctx)
        if not has_data:
            return MarketObserverOutput(
                role=self.role.value,
                version="v1",
                generated_at=datetime.now(timezone.utc),
                summary="insufficient_data — no market/theme/notice context provided",
                has_data=False,
                market_breadth=None,
                volume_flow=None,
                top_movers=(),
                sector_flows=(),
                volatility_regime=None,
                data_health=summarize_data_health(market_ctx),
                notice_observation=None,
                theme_observation=None,
                kimp_context=dict(kimp_ctx) if kimp_ctx else None,
                funding_context=dict(funding_ctx) if funding_ctx else None,
                findings=(AgentFinding(
                    kind="insufficient_data",
                    severity="WARNING",
                    message="market_context / theme_context / notice_context 모두 미수신",
                ),),
            )

        breadth = summarize_market_breadth(market_ctx)
        volume = summarize_volume_flow(market_ctx)
        movers = detect_top_movers(market_ctx)
        sectors = summarize_sector_flow(market_ctx, theme_ctx)
        volatility = summarize_volatility_regime(market_ctx)
        data_health = summarize_data_health(market_ctx)

        notice_obs: dict | None
        if notice_ctx:
            notice_obs = {
                "total_notices": int(notice_ctx.get("total_notices", 0) or 0),
                "high_risk_symbols": list(notice_ctx.get("high_risk_symbols") or ()),
                "candidate_filter_flags": list(
                    notice_ctx.get("candidate_filter_flags") or (),
                ),
                "human_summary": str(notice_ctx.get("human_summary", "")),
            }
        else:
            notice_obs = None

        theme_obs: dict | None
        if theme_ctx:
            themes = theme_ctx.get("themes") or theme_ctx.get("active_themes") or []
            theme_obs = {
                "active_theme_count": len(themes) if isinstance(themes, Sequence) else 0,
                "human_summary": str(theme_ctx.get("human_summary", "")),
            }
        else:
            theme_obs = None

        findings: list[AgentFinding] = []
        findings.append(AgentFinding(
            kind="breadth_observed",
            severity="INFO",
            message=f"breadth tone={breadth.risk_tone}",
            evidence={"risk_tone": breadth.risk_tone,
                       "advance_decline_ratio": breadth.advance_decline_ratio},
        ))
        if breadth.risk_tone == RiskTone.RISK_OFF:
            findings.append(AgentFinding(
                kind="risk_off_observed",
                severity="WARNING",
                message="declining ratio elevated — risk-off tone observed",
                evidence={"declining_count": breadth.declining_count,
                           "total_symbols": breadth.total_symbols},
            ))
        if volatility.transition_risk:
            findings.append(AgentFinding(
                kind="volatility_transition_risk",
                severity="WARNING",
                message="high volatility with sharp drops — transition risk observed",
                evidence={"avg_volatility": volatility.avg_volatility,
                           "high_vol_symbols": list(
                               volatility.high_volatility_symbols)},
            ))
        if data_health.freshness_ok is False or data_health.stale_symbols:
            findings.append(AgentFinding(
                kind="data_freshness_warning",
                severity="WARNING",
                message="freshness issues observed",
                evidence={"stale_symbols": list(data_health.stale_symbols)},
            ))
        if data_health.quality_excluded_count > 0:
            findings.append(AgentFinding(
                kind="data_quality_exclude_observed",
                severity="HIGH",
                message=(
                    f"{data_health.quality_excluded_count} symbols data_quality=EXCLUDE"
                ),
                evidence={"excluded_count": data_health.quality_excluded_count},
            ))

        summary_parts: list[str] = [f"breadth={breadth.risk_tone}"]
        summary_parts.append(f"volatility={volatility.volatility_tone}")
        summary_parts.append(f"top_movers={len(movers)}")
        summary_parts.append(f"sectors={len(sectors)}")
        summary = "Market environment observation: " + ", ".join(summary_parts)

        return MarketObserverOutput(
            role=self.role.value,
            version="v1",
            generated_at=datetime.now(timezone.utc),
            summary=summary,
            has_data=True,
            market_breadth=breadth,
            volume_flow=volume,
            top_movers=movers,
            sector_flows=sectors,
            volatility_regime=volatility,
            data_health=data_health,
            notice_observation=notice_obs,
            theme_observation=theme_obs,
            kimp_context=dict(kimp_ctx) if kimp_ctx else None,
            funding_context=dict(funding_ctx) if funding_ctx else None,
            findings=tuple(findings),
        )

    def evaluate(self, input: AgentInput) -> AgentOutput:
        """``StructuredAgentBase`` contract 충족 — AgentOutput 반환.

        ``observe()`` 의 MarketObserverOutput 을 AgentDecision.findings 로 평탄화한다.
        """
        rich = self.observe(input)
        decision = AgentDecision(
            role=self.role.value,
            summary=rich.summary,
            findings=rich.findings,
            recommendations=(),
        )
        return self.make_output(decision)


__all__ = (
    "RiskTone",
    "SectorTone",
    "VolatilityTone",
    "MarketBreadthSnapshot",
    "VolumeFlowSummary",
    "TopMover",
    "SectorFlow",
    "VolatilityRegimeSummary",
    "DataHealthSummary",
    "MarketObserverOutput",
    "MarketObserverAgent",
    "summarize_market_breadth",
    "summarize_volume_flow",
    "detect_top_movers",
    "summarize_sector_flow",
    "summarize_volatility_regime",
    "summarize_data_health",
)
