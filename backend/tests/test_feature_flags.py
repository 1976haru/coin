"""체크리스트 #10 Feature Flags — 회귀 테스트 (신규 gate API).

본 테스트는 `app.core.feature_flags` 의 신규 함수
(`is_*_enabled`, `assert_feature_allowed`, `public_snapshot`) 를 검증한다.
기존 `FeatureFlags` (frozen dataclass) 는 별도 테스트(`test_modes_flags.py`)
가 계속 책임진다.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.core import settings as settings_mod
from app.core.settings import Settings, reset_app_settings_cache
from app.core.feature_flags import (
    FeatureDisabledError,
    assert_feature_allowed,
    is_ai_execution_enabled,
    is_crypto_futures_live_enabled,
    is_kimp_strategy_enabled,
    is_live_trading_enabled,
    public_snapshot,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    reset_app_settings_cache()
    yield
    reset_app_settings_cache()


@pytest.fixture
def _isolated(monkeypatch, tmp_path):
    """AUTOTRADE_* env 제거 + YAML 격리 — default 만 보이게."""
    for k in list(os.environ):
        if k.startswith("AUTOTRADE_"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(
        settings_mod, "_default_yaml_path",
        lambda: tmp_path / "no_config.yaml",
    )
    return tmp_path


# ── 기본값: 모두 False ────────────────────────────────────────────

def test_all_risk_flags_default_false(_isolated):
    s = Settings()
    assert s.flags.enable_live_trading is False
    assert s.flags.enable_ai_execution is False
    assert s.flags.enable_crypto_futures_live is False
    assert s.flags.enable_kimp_strategy is False


def test_is_live_trading_default_false(_isolated):
    s = Settings()
    assert is_live_trading_enabled(s) is False


def test_is_ai_execution_default_false(_isolated):
    s = Settings()
    assert is_ai_execution_enabled(s) is False


def test_is_crypto_futures_live_default_false(_isolated):
    s = Settings()
    assert is_crypto_futures_live_enabled(s) is False


def test_is_kimp_strategy_default_false(_isolated):
    s = Settings()
    assert is_kimp_strategy_enabled(s) is False


# ── live_trading: 다중 조건 ──────────────────────────────────────

def test_live_trading_single_flag_not_enough(_isolated):
    """enable_live_trading=True 만으로는 활성되지 않는다."""
    s = Settings(
        flags={"enable_live_trading": True},
        # mode 는 기본 paper, allow_live_trading 도 False
    )
    assert is_live_trading_enabled(s) is False


def test_live_trading_needs_mode_live(_isolated):
    """mode 가 live 가 아니면 차단."""
    s = Settings(
        flags={"enable_live_trading": True},
        trading={"mode": "paper", "allow_live_trading": True,
                 "require_approval_for_live": True},
    )
    assert is_live_trading_enabled(s) is False


def test_live_trading_needs_allow_live_trading(_isolated):
    """allow_live_trading=False 면 차단 (Settings model_validator 도 같이 검증됨)."""
    # mode=live + allow=False 조합은 Settings 자체가 거부 → pydantic ValidationError
    with pytest.raises(Exception):
        Settings(
            flags={"enable_live_trading": True},
            trading={"mode": "live", "allow_live_trading": False},
        )


def test_live_trading_needs_require_approval_for_live(_isolated):
    """require_approval_for_live=False 는 feature gate 가 거부."""
    s = Settings(
        flags={"enable_live_trading": True},
        trading={"mode": "live", "allow_live_trading": True,
                 "require_approval_for_live": False},
    )
    assert is_live_trading_enabled(s) is False


def test_live_trading_all_conditions_true_returns_true(_isolated):
    """4 조건 모두 만족 → True."""
    s = Settings(
        flags={"enable_live_trading": True},
        trading={"mode": "live", "allow_live_trading": True,
                 "require_approval_for_live": True},
        app={"env": "prod"},  # local 이 아니어야 의미상 운영 가까운 상태
    )
    assert is_live_trading_enabled(s) is True


# ── ai_execution: AI는 직접 주문 권한 아님 ───────────────────────

def test_ai_execution_flag_enables_module(_isolated):
    """enable_ai_execution=True 만으로 *판단 모듈* 활성 OK."""
    s = Settings(flags={"enable_ai_execution": True})
    assert is_ai_execution_enabled(s) is True


def test_ai_execution_does_not_require_live(_isolated):
    """live 가 꺼져 있어도 AI 판단 모듈은 활성 가능 (paper/mock 범위)."""
    s = Settings(
        flags={"enable_ai_execution": True},
        trading={"mode": "paper"},
    )
    assert is_ai_execution_enabled(s) is True


# ── crypto_futures_live: local 환경 강제 False ───────────────────

def test_crypto_futures_live_blocked_in_local(_isolated):
    """app.env=local 이면 다른 조건이 다 켜져 있어도 False."""
    s = Settings(
        flags={
            "enable_live_trading": True,
            "enable_crypto_futures_live": True,
        },
        trading={"mode": "live", "allow_live_trading": True,
                 "require_approval_for_live": True},
        app={"env": "local"},
    )
    assert is_crypto_futures_live_enabled(s) is False


def test_crypto_futures_live_requires_live_trading_enabled(_isolated):
    """crypto futures live 는 live_trading 도 켜져야 한다."""
    s = Settings(
        flags={"enable_crypto_futures_live": True},
        trading={"mode": "live", "allow_live_trading": True,
                 "require_approval_for_live": True},
        app={"env": "prod"},
        # enable_live_trading 은 False → is_live_trading_enabled() False
    )
    assert is_crypto_futures_live_enabled(s) is False


def test_crypto_futures_live_all_conditions_outside_local_true(_isolated):
    s = Settings(
        flags={
            "enable_live_trading": True,
            "enable_crypto_futures_live": True,
        },
        trading={"mode": "live", "allow_live_trading": True,
                 "require_approval_for_live": True},
        app={"env": "prod"},
    )
    assert is_crypto_futures_live_enabled(s) is True


# ── kimp_strategy: 실거래 분리 ──────────────────────────────────

def test_kimp_strategy_enabled_in_paper_mode(_isolated):
    """live 가 꺼진 paper mode 에서도 kimp 전략 활성화 가능."""
    s = Settings(
        flags={"enable_kimp_strategy": True},
        trading={"mode": "paper", "allow_live_trading": False},
    )
    assert is_kimp_strategy_enabled(s) is True
    # 단, live 는 분리되어 여전히 False.
    assert is_live_trading_enabled(s) is False


def test_kimp_strategy_independent_of_live_trading(_isolated):
    s = Settings(
        flags={"enable_kimp_strategy": True, "enable_live_trading": False},
    )
    assert is_kimp_strategy_enabled(s) is True


# ── assert_feature_allowed ──────────────────────────────────────

def test_assert_live_trading_blocked_by_default(_isolated):
    s = Settings()
    with pytest.raises(FeatureDisabledError, match="live_trading"):
        assert_feature_allowed("live_trading", s)


def test_assert_crypto_futures_live_blocked_local(_isolated):
    s = Settings(
        flags={
            "enable_live_trading": True,
            "enable_crypto_futures_live": True,
        },
        trading={"mode": "live", "allow_live_trading": True,
                 "require_approval_for_live": True},
        app={"env": "local"},
    )
    with pytest.raises(FeatureDisabledError, match="crypto_futures_live"):
        assert_feature_allowed("crypto_futures_live", s)


def test_assert_unknown_feature_raises_valueerror(_isolated):
    s = Settings()
    with pytest.raises(ValueError, match="unknown feature"):
        assert_feature_allowed("nuclear_launch", s)


def test_assert_kimp_strategy_passes_when_enabled(_isolated):
    s = Settings(flags={"enable_kimp_strategy": True})
    assert_feature_allowed("kimp_strategy", s)  # raises 0건이면 통과


def test_assert_message_does_not_contain_secret(_isolated):
    """차단 에러 메시지에 secret 류 키워드가 노출되지 않아야 한다."""
    s = Settings(
        broker={"api_key": "ABCD-1234", "api_secret": "SHHH"},
    )
    with pytest.raises(FeatureDisabledError) as excinfo:
        assert_feature_allowed("live_trading", s)
    msg = str(excinfo.value)
    forbidden = ["ABCD-1234", "SHHH", "api_key", "api_secret"]
    for w in forbidden:
        assert w not in msg, f"error message leaks secret-ish text: {w}"


# ── public_snapshot: secret 무포함 ───────────────────────────────

def test_public_snapshot_has_no_secrets(_isolated):
    """broker.api_key/api_secret 가 채워져 있어도 snapshot 에는 노출 X."""
    s = Settings(
        broker={
            "api_key": "ABCD-1234",
            "api_secret": "SHHH",
            "account_no": "9-9-9",
        },
        database={"url": "postgresql://u:p@h/db"},
    )
    snap = public_snapshot(s)
    flat = repr(snap)
    forbidden = ["ABCD-1234", "SHHH", "9-9-9", "postgresql", "api_key",
                 "api_secret", "account_no", "token", "password"]
    for w in forbidden:
        assert w not in flat, f"snapshot leaks: {w}"


def test_public_snapshot_structure(_isolated):
    s = Settings()
    snap = public_snapshot(s)
    assert set(snap.keys()) == {"features", "context"}
    assert set(snap["features"].keys()) == {
        "live_trading", "ai_execution", "crypto_futures_live", "kimp_strategy",
    }
    assert snap["context"]["mode"] == "paper"
    assert snap["context"]["env"] == "local"
    # 위험 플래그 default 모두 False
    assert all(v is False for v in snap["features"].values())


# ── 우선순위: env override 적용 ──────────────────────────────────

def test_env_var_override_flags(_isolated, monkeypatch):
    monkeypatch.setenv("AUTOTRADE_FLAGS__ENABLE_KIMP_STRATEGY", "true")
    s = Settings()
    assert s.flags.enable_kimp_strategy is True
    assert is_kimp_strategy_enabled(s) is True
