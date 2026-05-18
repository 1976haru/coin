"""StrategySelectionAgent — 체크리스트 #29 (hook only).

장세별로 어떤 전략을 활성화할지 결정하는 agent. **본 단계에서는 interface/hook
만** 제공한다. 본격 구현(LLM 기반 또는 정교한 휴리스틱) 은 후속 단계.

설계 원칙 (CLAUDE.md §2.3):
  - **결정은 후보 활성화만** — direct_order_allowed=False 영구.
  - broker/adapter/order_gateway 를 알지 못한다.
  - registry 의 메타데이터(regime/symbol/enabled)만 보고 후보를 추린다.
  - 실제 신호 생성/주문 변환은 별도 단계.

본 모듈은 `app.strategies.contract_registry.ContractRegistry` 를 import 한다.
compliance 등 meta-checker 외 일반 agent 가 brokers/execution 을 import 하지
않는 것은 별도 정적 회귀로 강제된다.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any

from app.strategies.contract_registry import ContractRegistry, _Entry


# ── Context / Decision ───────────────────────────────────────────


@dataclass(frozen=True)
class StrategyActivationContext:
    """activation 판단 입력 — *읽기 전용*.

    필드는 모두 optional — 운영자가 점진적으로 채운다.
    """

    symbol: str | None = None
    regime: str = "UNKNOWN"                 # TREND_UP/TREND_DOWN/RANGE/UNKNOWN
    vol_band: str = "UNKNOWN"               # LOW/NORMAL/HIGH/UNKNOWN
    notice_high_risk_count: int = 0         # #18 NoticeContextBuilder 결과
    theme_review_required: bool = False     # #19 ThemeContextBuilder
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StrategyActivationDecision:
    """activation 결과.

    `direct_order_allowed=False` 영구 — 본 결정 자체로 주문이 일어나지 않는다.
    """

    generated_at: str
    regime: str
    symbol: str | None
    activated: tuple[str, ...]              # 후보 전략 이름들
    skipped: tuple[str, ...]                # filter 에 의해 제외된 전략
    skipped_reasons: dict[str, str]         # 전략별 제외 사유
    notes: tuple[str, ...] = ()
    direct_order_allowed: bool = False
    used_for_order: bool = False

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["activated"] = list(self.activated)
        d["skipped"] = list(self.skipped)
        d["notes"] = list(self.notes)
        return d


# ── Hook ─────────────────────────────────────────────────────────


def select_active_strategies(
    context: StrategyActivationContext,
    registry: ContractRegistry,
) -> StrategyActivationDecision:
    """장세별 전략 활성화 후보를 결정한다 (hook — 본격 구현은 후속).

    현재 휴리스틱 (보수적):
      1. ``registry.filter_enabled()`` 로 enabled 전략만 1차 후보.
         enabled 가 하나도 없으면 모든 등록된 전략을 후보로 함.
      2. ``context.regime`` 과 ``preferred_regimes`` 가 일치하는 것만 통과.
         ``UNKNOWN`` 은 모든 전략과 호환 (보수적 inclusion).
      3. ``context.notice_high_risk_count >= 1`` 또는
         ``context.theme_review_required`` 가 True 면 모든 전략을 *후보 유지하되*
         결정에 `notes` 를 추가한다 — 본 hook 은 차단하지 않는다 (Risk/OrderGuard
         단계의 책임).
      4. pair-전략(``capability.supports_pair=True``)은 context.symbol 이 None
         이거나 단일 symbol 일 때 제외 — pair 전용 활성화는 후속 단계 hook.

    본 hook 은 *전략을 실행하지 않는다*. 단지 어떤 전략이 후보인지만 반환.
    """
    candidates: list[_Entry] = registry.filter_enabled()
    if not candidates:
        # enabled 가 0개면 전체를 후보로 — registry 가 비어있는 운영 단계 backup.
        candidates = registry.all_entries()

    regime = (context.regime or "UNKNOWN").upper()
    activated: list[str] = []
    skipped: list[str] = []
    reasons: dict[str, str] = {}

    for e in candidates:
        name = e.capability.name
        prefs = tuple(p.upper() for p in e.preferred_regimes)

        # pair 전략은 단일 symbol context 에서 제외
        if e.capability.supports_pair and (
            context.symbol is None or "," not in (context.symbol or "")
        ):
            skipped.append(name)
            reasons[name] = "pair_strategy_requires_two_symbols"
            continue

        # regime 일치 검사 (UNKNOWN 은 통과)
        if regime != "UNKNOWN" and "ANY" not in prefs and regime not in prefs:
            skipped.append(name)
            reasons[name] = f"regime_mismatch: context={regime}, prefs={list(prefs)}"
            continue

        activated.append(name)

    notes: list[str] = []
    if context.notice_high_risk_count > 0:
        notes.append(
            f"notice_high_risk_count={context.notice_high_risk_count} — "
            "후보 활성화는 유지, Risk/OrderGuard 단계에서 추가 검증 권장."
        )
    if context.theme_review_required:
        notes.append(
            "theme_context.review_required=True — Risk/OrderGuard 단계에서 추가 검증 권장."
        )
    notes.append(
        "StrategySelectionAgent hook (#29) — 본격 구현은 후속 단계에서 고도화."
    )

    return StrategyActivationDecision(
        generated_at=datetime.now(timezone.utc).isoformat(),
        regime=regime,
        symbol=context.symbol,
        activated=tuple(activated),
        skipped=tuple(skipped),
        skipped_reasons=reasons,
        notes=tuple(notes),
    )


__all__ = (
    "StrategyActivationContext",
    "StrategyActivationDecision",
    "select_active_strategies",
)
