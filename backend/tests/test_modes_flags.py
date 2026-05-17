"""Operating modes + Feature flags 기본값 회귀 테스트.

체크리스트 #3 Operating Modes + #10 Feature Flags.
실수로 위험 플래그가 기본 true 가 되는 것을 막는다.
"""
import os

import pytest

from app.core.modes import TradingMode


# ── 운용 모드 enum 보장 ────────────────────────────────────────────

def test_all_six_modes_defined():
    expected = {
        "SIMULATION", "PAPER", "LIVE_SHADOW",
        "LIVE_MANUAL_APPROVAL", "LIVE_AI_ASSIST", "LIVE_AI_EXECUTION",
    }
    assert {m.value for m in TradingMode} == expected


def test_paper_does_not_allow_real_order():
    assert TradingMode.PAPER.allows_real_order is False
    assert TradingMode.SIMULATION.allows_real_order is False


def test_live_modes_allow_real_order():
    assert TradingMode.LIVE_MANUAL_APPROVAL.allows_real_order
    assert TradingMode.LIVE_AI_ASSIST.allows_real_order
    assert TradingMode.LIVE_AI_EXECUTION.allows_real_order


def test_only_ai_execution_allows_auto():
    for m in TradingMode:
        if m == TradingMode.LIVE_AI_EXECUTION:
            assert m.allows_ai_auto_execute
        else:
            assert not m.allows_ai_auto_execute


# ── Feature Flags 기본값 ──────────────────────────────────────────

DANGEROUS_FLAGS = [
    "ENABLE_LIVE_TRADING",
    "ENABLE_AI_EXECUTION",
    "ENABLE_CRYPTO_FUTURES_LIVE",
    "ENABLE_LIVE_ORDER_SUBMISSION",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """위험 플래그를 환경에서 제거 — pure default 동작 검증."""
    for k in DANGEROUS_FLAGS:
        monkeypatch.delenv(k, raising=False)
    yield


def _fresh_flags():
    """현재 환경변수 기준으로 FeatureFlags 인스턴스 생성.

    field(default_factory=...) 덕분에 인스턴스화 시점에 env 가 평가된다 —
    importlib.reload 가 필요 없어 cross-test 모듈 식별자 오염도 사라진다.
    """
    from app.core.feature_flags import get_feature_flags
    return get_feature_flags()


def test_dangerous_flags_default_false():
    f = _fresh_flags()
    assert f.enable_live_trading is False
    assert f.enable_ai_execution is False
    assert f.enable_crypto_futures_live is False
    assert f.enable_live_order_submission is False


def test_withdrawal_flag_permanently_false(monkeypatch):
    """ENABLE_WITHDRAWAL 은 환경변수로도 켜지지 않는다."""
    monkeypatch.setenv("ENABLE_WITHDRAWAL", "true")
    f = _fresh_flags()
    assert f.enable_withdrawal is False


def test_safety_summary_lists_all_flags():
    f = _fresh_flags()
    summary = f.safety_summary()
    for k in DANGEROUS_FLAGS:
        assert k in summary
    assert "ENABLE_WITHDRAWAL" in summary
    # 모두 false 여야 (기본 호출이므로)
    assert summary["ENABLE_LIVE_TRADING"] is False
    assert summary["ENABLE_WITHDRAWAL"] is False
