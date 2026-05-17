"""체크리스트 #9 Config Layer — 신규 pydantic-settings 회귀 테스트.

본 테스트는 신규 `app.core.settings.Settings` (nested + SecretStr + YAML) 를
검증한다. 기존 `app.core.config.Settings` (frozen dataclass) 는 별도 테스트
파일(`test_config_layer.py`) 이 계속 책임진다.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from app.core import settings as settings_mod
from app.core.settings import (
    Settings,
    get_app_settings,
    load_yaml_config,
    reset_app_settings_cache,
)
from app.schemas.enums import TradingMode


@pytest.fixture(autouse=True)
def _clear_cache():
    reset_app_settings_cache()
    yield
    reset_app_settings_cache()


@pytest.fixture
def _no_env(monkeypatch):
    """AUTOTRADE_* 환경변수 제거 — default + YAML 만 보고 싶을 때 사용."""
    import os
    for k in list(os.environ):
        if k.startswith("AUTOTRADE_"):
            monkeypatch.delenv(k, raising=False)
    return monkeypatch


@pytest.fixture
def _isolated_yaml(monkeypatch, tmp_path):
    """YAML 소스를 빈 임시 디렉토리로 격리. 저장소의 config/config.yaml 영향 차단."""
    empty = tmp_path / "no_config.yaml"
    monkeypatch.setattr(settings_mod, "_default_yaml_path", lambda: empty)
    return tmp_path


# ── 기본 동작 ────────────────────────────────────────────────────

def test_get_settings_returns_settings_instance(_no_env, _isolated_yaml):
    s = get_app_settings()
    assert isinstance(s, Settings)


def test_default_app_name_is_autotrade_backend(_no_env, _isolated_yaml):
    s = Settings()
    assert s.app.name == "AutoTrade Backend"


def test_default_trading_mode_is_paper(_no_env, _isolated_yaml):
    s = Settings()
    assert s.trading.mode == TradingMode.PAPER
    assert s.trading.mode.value == "paper"


def test_default_emergency_stop_enabled_true(_no_env, _isolated_yaml):
    s = Settings()
    assert s.risk.emergency_stop_enabled is True


def test_default_logging_level_info(_no_env, _isolated_yaml):
    s = Settings()
    assert s.logging.level == "INFO"


def test_default_allow_live_trading_false(_no_env, _isolated_yaml):
    s = Settings()
    assert s.trading.allow_live_trading is False
    assert s.trading.require_approval_for_live is True


# ── live mode 안전 가드 ──────────────────────────────────────────

def test_live_mode_without_allow_live_trading_fails(_no_env, _isolated_yaml):
    with pytest.raises(ValidationError):
        Settings(trading={"mode": "live", "allow_live_trading": False})


def test_live_mode_with_allow_live_trading_passes(_no_env, _isolated_yaml):
    s = Settings(trading={"mode": "live", "allow_live_trading": True})
    assert s.trading.mode == TradingMode.LIVE
    # 단, require_approval_for_live 기본 True 가 유지되어야 한다.
    assert s.trading.require_approval_for_live is True


# ── YAML secret guard ────────────────────────────────────────────

def test_yaml_with_secret_key_raises(tmp_path: Path):
    # test fixture — placeholder values, scanner-safe (well under 20-char pattern)
    bad = tmp_path / "config.yaml"
    bad.write_text(textwrap.dedent("""
        broker:
          provider: "mock"
          api_key: "x"
    """).strip(), encoding="utf-8")
    with pytest.raises(ValueError, match="secret"):
        load_yaml_config(bad)


def test_yaml_secret_guard_catches_nested(tmp_path: Path):
    # test fixture — placeholder
    bad = tmp_path / "config.yaml"
    bad.write_text(textwrap.dedent("""
        database:
          access_token: "x"
    """).strip(), encoding="utf-8")
    with pytest.raises(ValueError, match="secret"):
        load_yaml_config(bad)


def test_yaml_missing_file_returns_empty(tmp_path: Path):
    missing = tmp_path / "nope.yaml"
    assert load_yaml_config(missing) == {}


def test_yaml_invalid_syntax_raises(tmp_path: Path):
    bad = tmp_path / "config.yaml"
    bad.write_text("app: [unterminated\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="파싱 실패"):
        load_yaml_config(bad)


# ── SecretStr 보호 ───────────────────────────────────────────────

def test_secretstr_not_exposed_in_repr(_no_env, _isolated_yaml):
    # placeholder values — short to stay below security-scan pattern threshold
    s = Settings(broker={
        "provider": "mock",
        "api_key": "ABCD-1234",
        "api_secret": "SHHH",
        "account_no": "9-9-9",
    })
    text = repr(s)
    assert "ABCD-1234" not in text
    assert "SHHH" not in text
    assert "9-9-9" not in text


def test_secretstr_get_secret_value_works(_no_env, _isolated_yaml):
    s = Settings(broker={"api_key": "real"})
    assert isinstance(s.broker.api_key, SecretStr)
    assert s.broker.api_key.get_secret_value() == "real"


def test_safe_dump_masks_secrets(_no_env, _isolated_yaml):
    s = Settings(broker={
        "api_key": "ABCD-1234",
        "api_secret": "SHHH",
    }, database={"url": "postgresql://u:p@h/db"})
    dumped = s.safe_dump()
    flat = repr(dumped)
    assert "ABCD-1234" not in flat
    assert "SHHH" not in flat
    assert "postgresql://u:p@h/db" not in flat


# ── 우선순위: env > YAML ─────────────────────────────────────────

def test_env_overrides_yaml(tmp_path: Path, monkeypatch):
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(textwrap.dedent("""
        trading:
          mode: "mock"
        logging:
          level: "WARNING"
    """).strip(), encoding="utf-8")
    monkeypatch.setattr(settings_mod, "_default_yaml_path", lambda: yaml_path)

    # OS env 가 YAML 보다 우선해야 한다.
    monkeypatch.setenv("AUTOTRADE_TRADING__MODE", "paper")
    monkeypatch.setenv("AUTOTRADE_LOGGING__LEVEL", "DEBUG")

    s = Settings()
    assert s.trading.mode == TradingMode.PAPER  # env override
    assert s.logging.level == "DEBUG"            # env override


def test_yaml_used_when_env_absent(tmp_path: Path, monkeypatch):
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(textwrap.dedent("""
        trading:
          mode: "mock"
        logging:
          level: "WARNING"
    """).strip(), encoding="utf-8")
    monkeypatch.setattr(settings_mod, "_default_yaml_path", lambda: yaml_path)

    # env 미설정 → YAML 값이 default 보다 우선.
    import os
    for k in list(os.environ):
        if k.startswith("AUTOTRADE_"):
            monkeypatch.delenv(k, raising=False)

    s = Settings()
    assert s.trading.mode == TradingMode.MOCK
    assert s.logging.level == "WARNING"


def test_env_nested_delimiter_works(_no_env, _isolated_yaml, monkeypatch):
    monkeypatch.setenv("AUTOTRADE_RISK__MAX_OPEN_POSITIONS", "7")
    monkeypatch.setenv("AUTOTRADE_RISK__EMERGENCY_STOP_ENABLED", "false")
    s = Settings()
    assert s.risk.max_open_positions == 7
    assert s.risk.emergency_stop_enabled is False


# ── 캐싱 동작 ────────────────────────────────────────────────────

def test_get_settings_caches(_no_env, _isolated_yaml):
    a = get_app_settings()
    b = get_app_settings()
    assert a is b


def test_cache_clear_returns_new_instance(_no_env, _isolated_yaml):
    a = get_app_settings()
    reset_app_settings_cache()
    b = get_app_settings()
    assert a is not b
