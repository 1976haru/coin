"""Feature Flags — 위험 기능 기본 비활성.

체크리스트 #10 Feature Flags. Settings에서 분리해 한 눈에 보이게 한다.
모든 ENABLE_* 는 기본 false (KIMP는 paper-only 의미로 default true 유지하되,
실제 진입은 PermissionGate가 모드와 함께 차단).
"""
import os
from dataclasses import dataclass


def _bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class FeatureFlags:
    enable_live_trading:           bool = _bool("ENABLE_LIVE_TRADING", False)
    enable_ai_execution:           bool = _bool("ENABLE_AI_EXECUTION", False)
    enable_crypto_futures_live:    bool = _bool("ENABLE_CRYPTO_FUTURES_LIVE", False)
    enable_kimp_strategy:          bool = _bool("ENABLE_KIMP_STRATEGY", True)   # paper-only 의미
    enable_live_order_submission:  bool = _bool("ENABLE_LIVE_ORDER_SUBMISSION", False)
    enable_withdrawal:             bool = False                                  # 영구 false
    enable_ai_agents:              bool = _bool("ENABLE_AI_AGENTS", False)
    enable_okx:                    bool = _bool("ENABLE_OKX", True)
    enable_upbit:                  bool = _bool("ENABLE_UPBIT", True)

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
