"""Feature Flags — 위험 기능 기본 비활성 (체크리스트 #10).

두 계층을 함께 제공한다:

(1) Legacy: `FeatureFlags` (frozen dataclass).
    OS env var (`ENABLE_LIVE_TRADING` 등) 직접 읽음. 1300+ 회귀 테스트와
    `app.core.config.Settings` 가 의존하므로 그대로 보존.

(2) New gate API (체크리스트 #10 스펙):
    - `is_live_trading_enabled()` — 다중 조건 (4개 모두 True 이어야 통과)
    - `is_ai_execution_enabled()` — AI 실행 *판단* 모듈 활성 여부 (직접 주문 X)
    - `is_crypto_futures_live_enabled()` — local 환경에서 강제 False
    - `is_kimp_strategy_enabled()` — strategy flag only, NOT execution permission
    - `assert_feature_allowed(name)` — 차단 시 `FeatureDisabledError` raise
    - `public_snapshot()` — UI/감사용 안전 dict (Secret 무포함)

기본 원칙 (CLAUDE.md §2):
  - 모든 위험 플래그 default False.
  - 단일 플래그만으로 실거래/AI 자동실행 활성되지 않는다 (다중 잠금).
  - feature flag 가 통과해도 실제 주문은 governance/execution 가 한 번 더 차단한다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Optional

from app.schemas.enums import TradingMode


def _bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


# ─────────────────────────────────────────────────────────────────
# (1) Legacy FeatureFlags — 기존 코드/테스트 호환 보존
# ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class FeatureFlags:
    enable_live_trading:           bool = field(default_factory=lambda: _bool("ENABLE_LIVE_TRADING", False))
    enable_ai_execution:           bool = field(default_factory=lambda: _bool("ENABLE_AI_EXECUTION", False))
    enable_crypto_futures_live:    bool = field(default_factory=lambda: _bool("ENABLE_CRYPTO_FUTURES_LIVE", False))
    enable_kimp_strategy:          bool = field(default_factory=lambda: _bool("ENABLE_KIMP_STRATEGY", True))   # paper-only 의미
    enable_live_order_submission:  bool = field(default_factory=lambda: _bool("ENABLE_LIVE_ORDER_SUBMISSION", False))
    enable_withdrawal:             bool = False                                  # 영구 false
    enable_ai_agents:              bool = field(default_factory=lambda: _bool("ENABLE_AI_AGENTS", False))
    enable_okx:                    bool = field(default_factory=lambda: _bool("ENABLE_OKX", True))
    enable_upbit:                  bool = field(default_factory=lambda: _bool("ENABLE_UPBIT", True))

    def safety_summary(self) -> dict:
        """관제 UI/감사 로그에 노출할 안전 상태 요약."""
        return {
            "ENABLE_LIVE_TRADING":           self.enable_live_trading,
            "ENABLE_AI_EXECUTION":           self.enable_ai_execution,
            "ENABLE_CRYPTO_FUTURES_LIVE":    self.enable_crypto_futures_live,
            "ENABLE_KIMP_STRATEGY":          self.enable_kimp_strategy,
            "ENABLE_LIVE_ORDER_SUBMISSION":  self.enable_live_order_submission,
            "ENABLE_WITHDRAWAL":             self.enable_withdrawal,
            "ENABLE_AI_AGENTS":              self.enable_ai_agents,
        }


def get_feature_flags() -> FeatureFlags:
    return FeatureFlags()


# ─────────────────────────────────────────────────────────────────
# (2) New gate API — 체크리스트 #10 스펙
# ─────────────────────────────────────────────────────────────────

class FeatureDisabledError(RuntimeError):
    """위험 feature 가 비활성/차단 상태일 때 raise.

    Secret 정보는 메시지에 포함하지 않는다 — feature name 과 차단 사유만 노출.
    """


# 지원 feature 이름 — assert_feature_allowed 의 contract.
_SUPPORTED_FEATURES = frozenset({
    "live_trading",
    "ai_execution",
    "crypto_futures_live",
    "kimp_strategy",
})


def _get_settings_safely():
    """신규 pydantic-settings Settings 를 캐시 경유로 가져온다.

    import 시점 부작용을 피하기 위해 lazy import.
    """
    from app.core.settings import get_app_settings
    return get_app_settings()


def is_live_trading_enabled(settings=None) -> bool:
    """실거래 송신이 *feature flag 관점에서* 열려 있는가.

    4개 조건을 모두 만족할 때만 True:
      1. flags.enable_live_trading == True
      2. trading.mode == "live"
      3. trading.allow_live_trading == True
      4. trading.require_approval_for_live == True
         (승인 *구조*를 요구한다는 의미. 실제 승인 검사는 governance 모듈이 담당.)

    이 함수는 "실거래 가능 확정" 이 아니라 "feature 잠금이 열렸는가" 만 알려준다.
    실제 주문 가능 여부는 governance/execution 가 추가로 검사한다.
    """
    s = settings or _get_settings_safely()
    return (
        bool(s.flags.enable_live_trading)
        and s.trading.mode == TradingMode.LIVE
        and bool(s.trading.allow_live_trading)
        and bool(s.trading.require_approval_for_live)
    )


def is_ai_execution_enabled(settings=None) -> bool:
    """AI *실행 판단 모듈* 을 활성화할 수 있는가.

    중요:
      - 본 함수가 True 라고 해서 AI 가 직접 주문을 송신할 수 있다는 뜻이 아니다.
      - AI 의 판단 결과는 항상 `RiskManager → OrderGuard → PermissionGate →
        ApprovalQueue → OrderGateway` 단일 경로를 통과해야만 실주문이 된다.
      - live trading 이 꺼져 있으면 본 함수가 True 여도 AI 는 paper/mock 범위에서만
        동작한다 (실제 LIVE 주문 routing 불가).

    조건:
      - flags.enable_ai_execution == True
    """
    s = settings or _get_settings_safely()
    return bool(s.flags.enable_ai_execution)


def is_crypto_futures_live_enabled(settings=None) -> bool:
    """코인/선물 실거래가 *feature flag 관점에서* 열려 있는가.

    추가 안전: local 환경(app.env == "local")에서는 무조건 False.
    이유: 로컬 개발환경에서 실수로 켜지지 않게 하기 위함.

    조건 (모두 만족):
      1. flags.enable_crypto_futures_live == True
      2. is_live_trading_enabled() == True
      3. trading.mode == "live"
      4. trading.allow_live_trading == True
      5. app.env != "local"
    """
    s = settings or _get_settings_safely()
    if s.app.env == "local":
        return False
    return (
        bool(s.flags.enable_crypto_futures_live)
        and is_live_trading_enabled(s)
        and s.trading.mode == TradingMode.LIVE
        and bool(s.trading.allow_live_trading)
    )


def is_kimp_strategy_enabled(settings=None) -> bool:
    """김프 전략 *모듈* 이 활성화될 수 있는가 — strategy flag only.

    NOT execution permission: 본 함수가 True 라고 해서 실거래가 허용되는 것이
    아니다. 단지 김프 전략 코드를 실행하고 신호를 만들 수 있다는 의미이며,
    live trading 이 꺼져 있어도 paper/mock 에서 전략 검증은 가능해야 한다.

    조건:
      - flags.enable_kimp_strategy == True
    """
    s = settings or _get_settings_safely()
    return bool(s.flags.enable_kimp_strategy)


_FEATURE_GUARDS = {
    "live_trading":        is_live_trading_enabled,
    "ai_execution":        is_ai_execution_enabled,
    "crypto_futures_live": is_crypto_futures_live_enabled,
    "kimp_strategy":       is_kimp_strategy_enabled,
}


def assert_feature_allowed(feature_name: str, settings=None) -> None:
    """위험 기능 호출 직전 차단용.

    Parameters
    ----------
    feature_name:
        지원: "live_trading" | "ai_execution" | "crypto_futures_live" | "kimp_strategy"

    Raises
    ------
    FeatureDisabledError
        feature 가 활성 조건을 만족하지 않을 때.
    ValueError
        지원되지 않는 feature 이름일 때.
    """
    name = (feature_name or "").strip().lower()
    if name not in _SUPPORTED_FEATURES:
        raise ValueError(
            f"unknown feature: {feature_name!r} (supported: "
            f"{sorted(_SUPPORTED_FEATURES)})"
        )
    guard = _FEATURE_GUARDS[name]
    if not guard(settings):
        # Secret 정보 없음 — feature 이름과 일반적 차단 사유만.
        raise FeatureDisabledError(
            f"feature '{name}' is disabled or its safety conditions are not met"
        )


def public_snapshot(settings=None) -> dict:
    """UI / health 응답용 안전 snapshot.

    포함:
      - 4개 feature 별 *효과적* 활성 여부 (모든 안전 조건 평가 결과)
      - 평가 컨텍스트 (mode/env) — secret 없음

    제외:
      - api_key / secret / token / account_no 등 모든 Secret 류 값
    """
    s = settings or _get_settings_safely()
    return {
        "features": {
            "live_trading":        is_live_trading_enabled(s),
            "ai_execution":        is_ai_execution_enabled(s),
            "crypto_futures_live": is_crypto_futures_live_enabled(s),
            "kimp_strategy":       is_kimp_strategy_enabled(s),
        },
        "context": {
            "mode": s.trading.mode.value,
            "env":  s.app.env,
        },
    }


# ── 캐싱 ──────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _cached_snapshot_key() -> Optional[int]:
    """캐시 ID — Settings 캐시와 동조."""
    return id(_get_settings_safely())


def reset_feature_flags_cache() -> None:
    """테스트 헬퍼."""
    _cached_snapshot_key.cache_clear()
