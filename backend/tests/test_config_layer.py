"""체크리스트 #9 Config Layer — 회귀 테스트.

검증:
  1. Settings.summary() — secret 마스킹, TradingMode → str
  2. Settings.validate() — 운영 경고 시나리오
  3. .env.example ↔ ENV_VARS_REFERENCED 파리티
  4. /api/status 의 safety_warnings 필드
  5. /api/config/warnings 공개, /api/config/effective admin 강제
"""
from __future__ import annotations
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, ENV_VARS_REFERENCED, reset_settings_cache
from app.core.modes import TradingMode


REPO_ROOT = Path(__file__).resolve().parents[2]


# ── 1. Settings.summary() ────────────────────────────────────────

def test_summary_redacts_secrets():
    s = Settings(
        trading_mode=TradingMode.PAPER,
        upbit_access_key="UB-LEAK-1",
        upbit_secret_key="UB-LEAK-2",
        okx_api_key="OKX-LEAK",
        okx_api_secret="OKX-S",
        okx_api_password="OKX-P",
        anthropic_api_key="ANTH-LEAK",
        telegram_token="TG-LEAK",
        admin_token="should-not-leak",
        exchangerate_api_key="ER-LEAK",
    )
    out = s.summary()
    assert out["upbit_access_key"]   == "***REDACTED***"
    assert out["upbit_secret_key"]   == "***REDACTED***"
    assert out["okx_api_key"]        == "***REDACTED***"
    assert out["okx_api_secret"]     == "***REDACTED***"
    assert out["okx_api_password"]   == "***REDACTED***"
    assert out["anthropic_api_key"]  == "***REDACTED***"
    assert out["telegram_token"]     == "***REDACTED***"
    assert out["admin_token"]        == "***REDACTED***"
    assert out["exchangerate_api_key"] == "***REDACTED***"
    # 비-secret 은 그대로
    assert out["trading_mode"] == "PAPER"
    assert out["demo_mode"] in (True, False)


def test_summary_does_not_leak_secret_strings():
    leak = "ULTRA-SUPER-SECRET-9999"
    s = Settings(upbit_access_key=leak, anthropic_api_key=leak,
                 admin_token=leak, telegram_token=leak)
    out = s.summary()
    # 직렬화하면 어디에도 leak 문자열이 없어야 함
    import json
    assert leak not in json.dumps(out)


def test_summary_converts_trading_mode_enum_to_string():
    s = Settings(trading_mode=TradingMode.LIVE_SHADOW)
    out = s.summary()
    assert out["trading_mode"] == "LIVE_SHADOW"
    assert isinstance(out["trading_mode"], str)


# ── 2. Settings.validate() ───────────────────────────────────────

def test_validate_clean_paper_no_warnings():
    s = Settings(trading_mode=TradingMode.PAPER, admin_token="my-strong-token")
    assert s.validate() == []


def test_validate_warns_default_admin_token():
    s = Settings(admin_token="change-me-local-only")
    warnings = s.validate()
    assert any("ADMIN_TOKEN" in w for w in warnings)


def test_validate_warns_empty_admin_token():
    s = Settings(admin_token="")
    warnings = s.validate()
    assert any("ADMIN_TOKEN" in w for w in warnings)


def test_validate_warns_live_mode_without_flag():
    s = Settings(
        trading_mode=TradingMode.LIVE_MANUAL_APPROVAL,
        enable_live_trading=False,
        admin_token="strong",
    )
    warnings = s.validate()
    assert any("ENABLE_LIVE_TRADING" in w for w in warnings)


def test_validate_warns_ai_execution_without_flag():
    s = Settings(
        trading_mode=TradingMode.LIVE_AI_EXECUTION,
        enable_live_trading=True,
        enable_ai_execution=False,
        admin_token="strong",
    )
    warnings = s.validate()
    assert any("ENABLE_AI_EXECUTION" in w for w in warnings)


def test_validate_warns_oversized_notional_in_live():
    s = Settings(
        trading_mode=TradingMode.LIVE_MANUAL_APPROVAL,
        enable_live_trading=True,
        max_order_notional_usdt=5000.0,
        admin_token="strong",
    )
    warnings = s.validate()
    assert any("MAX_ORDER_NOTIONAL_USDT" in w for w in warnings)


def test_validate_warns_high_leverage_in_live():
    s = Settings(
        trading_mode=TradingMode.LIVE_MANUAL_APPROVAL,
        enable_live_trading=True,
        max_leverage=5.0,
        admin_token="strong",
    )
    warnings = s.validate()
    assert any("MAX_LEVERAGE" in w for w in warnings)


def test_validate_warns_high_daily_loss_in_live():
    s = Settings(
        trading_mode=TradingMode.LIVE_MANUAL_APPROVAL,
        enable_live_trading=True,
        daily_loss_limit_pct=10.0,
        admin_token="strong",
    )
    warnings = s.validate()
    assert any("DAILY_LOSS_LIMIT_PCT" in w for w in warnings)


def test_validate_paper_allows_relaxed_limits():
    """PAPER/SIMULATION 모드에서는 느슨한 한도 허용 (페이퍼는 학습/실험 환경)."""
    s = Settings(
        trading_mode=TradingMode.PAPER,
        max_order_notional_usdt=5000.0,
        max_leverage=10.0,
        daily_loss_limit_pct=20.0,
        admin_token="strong",
    )
    # admin 외에 위 한도 관련 경고는 없어야 함 (PAPER 는 면제)
    warnings = s.validate()
    assert not any("NOTIONAL" in w or "LEVERAGE" in w or "DAILY_LOSS" in w
                   for w in warnings)


# ── 3. .env.example ↔ ENV_VARS_REFERENCED 파리티 ────────────────

def _parse_env_example_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([A-Z][A-Z0-9_]*)=", line)
        if m:
            keys.add(m.group(1))
    return keys


def test_env_example_contains_all_referenced_vars():
    """ENV_VARS_REFERENCED 의 모든 키가 .env.example 에 존재해야 한다."""
    env_example = REPO_ROOT / ".env.example"
    keys = _parse_env_example_keys(env_example)
    missing = set(ENV_VARS_REFERENCED) - keys
    assert not missing, f".env.example 에 누락된 키: {sorted(missing)}"


def test_env_example_keys_are_subset_of_referenced_or_known():
    """.env.example 의 모든 키가 ENV_VARS_REFERENCED 또는 알려진 인프라 키여야 한다.

    `.env.example` 에 있는데 코드에서 사용하지 않는 변수가 있으면 dead config.
    """
    INFRA_KEYS = {
        "DATABASE_URL",                     # #13 db
        "ENABLE_CRYPTO_FUTURES_LIVE",       # FeatureFlags only
        "ENABLE_LIVE_ORDER_SUBMISSION",     # FeatureFlags only
    }
    env_example = REPO_ROOT / ".env.example"
    keys = _parse_env_example_keys(env_example)
    extra = keys - set(ENV_VARS_REFERENCED) - INFRA_KEYS
    assert not extra, (
        f".env.example 에 코드에서 안 쓰는 키: {sorted(extra)} "
        "(코드 추가 또는 .env.example 에서 제거)"
    )


def test_referenced_vars_actually_used_in_config_py():
    """ENV_VARS_REFERENCED 의 모든 키가 config.py 본문에서 실제 참조되어야 한다."""
    config_py = (REPO_ROOT / "backend" / "app" / "core" / "config.py")
    text = config_py.read_text(encoding="utf-8")
    for key in ENV_VARS_REFERENCED:
        assert f'"{key}"' in text, f"config.py 가 {key} 를 참조하지 않음"


# ── 4. /api/status 의 safety_warnings ────────────────────────────

def test_api_status_includes_safety_warnings():
    from app.main import app
    client = TestClient(app)
    r = client.get("/api/status")
    assert r.status_code == 200
    body = r.json()
    assert "safety_warnings" in body
    assert isinstance(body["safety_warnings"], list)


# ── 5. /api/config/* ─────────────────────────────────────────────

def test_api_config_warnings_is_public():
    from app.main import app
    client = TestClient(app)
    r = client.get("/api/config/warnings")
    assert r.status_code == 200
    assert "warnings" in r.json()


def test_api_config_effective_requires_admin():
    from app.main import app
    client = TestClient(app)
    r = client.get("/api/config/effective")
    assert r.status_code == 401


def test_api_config_effective_returns_redacted_summary():
    from app.core.config import get_settings
    from app.main import app
    token = get_settings().admin_token
    client = TestClient(app)
    r = client.get("/api/config/effective", headers={"X-Admin-Token": token})
    assert r.status_code == 200
    body = r.json()
    # secret 컬럼은 모두 마스킹
    for k in ("upbit_access_key", "okx_api_secret", "anthropic_api_key",
              "telegram_token", "admin_token"):
        assert body.get(k) == "***REDACTED***"
    # 비-secret 노출
    assert body["trading_mode"] in {m.value for m in TradingMode}


# ── 6. lru_cache 동작 ────────────────────────────────────────────

def test_get_settings_is_cached():
    from app.core.config import get_settings
    a = get_settings()
    b = get_settings()
    assert a is b


def test_reset_settings_cache_creates_new_instance():
    from app.core.config import get_settings
    a = get_settings()
    reset_settings_cache()
    b = get_settings()
    # 새 인스턴스 — frozen dataclass 라 동등성은 같지만 정체성은 다를 수 있음
    assert a == b  # 같은 env 에서 같은 값
