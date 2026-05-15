"""체크리스트 #28 Sandbox/Paper Keys — 회귀 테스트.

검증:
  1. docs/sandbox_paper_keys.md 가 존재하고 핵심 섹션을 포함
  2. AdapterMode 4단계가 base.py 에 정의되어 있음
  3. Settings 가 SANDBOX 키 슬롯을 보유 (LIVE 와 분리)
  4. Settings.validate() 가 모드/키 불일치를 경고
  5. .env.example 에 sandbox 변수 명시
  6. README/CLAUDE.md/api_key_policy.md 와의 정합성
"""
from __future__ import annotations
import re
from pathlib import Path

import pytest

from app.core.config import Settings, ENV_VARS_REFERENCED
from app.core.modes import TradingMode


REPO_ROOT = Path(__file__).resolve().parents[2]
POLICY = REPO_ROOT / "docs" / "sandbox_paper_keys.md"


# ── 1. 정책 문서 ─────────────────────────────────────────────────

def test_policy_doc_exists():
    assert POLICY.is_file(), "docs/sandbox_paper_keys.md 가 존재해야 함 (#28)"


@pytest.mark.parametrize("section", [
    "Adapter Mode",
    "환경변수 네이밍 컨벤션",
    "절대 금지 패턴",
    "권장 구현 패턴",
    "운영 점검표",
])
def test_policy_doc_has_required_sections(section: str):
    text = POLICY.read_text(encoding="utf-8")
    assert section in text, f"sandbox_paper_keys.md 에 '{section}' 섹션 누락"


@pytest.mark.parametrize("phrase", [
    "READ_ONLY", "PAPER", "SANDBOX", "LIVE",
    "OKX_API_KEY_SANDBOX",
    "절대 섞지",
])
def test_policy_doc_contains_required_phrases(phrase: str):
    text = POLICY.read_text(encoding="utf-8")
    assert phrase in text, f"sandbox_paper_keys.md 에 '{phrase}' 표현 누락"


# ── 2. AdapterMode 4단계 정의 ────────────────────────────────────

def test_adapter_mode_has_four_tiers():
    """base.py 의 AdapterMode 가 READ_ONLY/PAPER/SANDBOX/LIVE 4 단계를 모두 포함."""
    text = (REPO_ROOT / "backend" / "app" / "brokers" / "base.py"
            ).read_text(encoding="utf-8")
    for tier in ("READ_ONLY", "PAPER", "SANDBOX", "LIVE"):
        assert f'"{tier}"' in text, f"AdapterMode 에 '{tier}' 누락"


# ── 3. Settings 의 SANDBOX 키 슬롯 ───────────────────────────────

def test_settings_has_okx_sandbox_slots():
    s = Settings()
    for attr in ("okx_api_key_sandbox", "okx_api_secret_sandbox",
                 "okx_api_password_sandbox"):
        assert hasattr(s, attr), f"Settings 에 {attr} 슬롯 누락"


def test_settings_has_binance_sandbox_slots():
    s = Settings()
    for attr in ("binance_api_key_sandbox", "binance_api_secret_sandbox"):
        assert hasattr(s, attr), f"Settings 에 {attr} 슬롯 누락"


def test_sandbox_keys_default_to_empty_string():
    s = Settings()
    assert s.okx_api_key_sandbox == ""
    assert s.okx_api_secret_sandbox == ""
    assert s.binance_api_key_sandbox == ""


def test_env_vars_referenced_includes_sandbox_keys():
    """ENV_VARS_REFERENCED 에 SANDBOX 키들이 등록 (.env.example 파리티)."""
    for key in ("OKX_API_KEY_SANDBOX", "OKX_API_SECRET_SANDBOX",
                "OKX_API_PASSWORD_SANDBOX",
                "BINANCE_API_KEY_SANDBOX", "BINANCE_API_SECRET_SANDBOX"):
        assert key in ENV_VARS_REFERENCED, f"{key} 가 카탈로그에 누락"


# ── 4. Settings.validate 의 모드/키 정합성 ───────────────────────

def test_validate_warns_live_keys_in_paper_mode():
    s = Settings(
        trading_mode=TradingMode.PAPER,
        admin_token="strong",
        okx_api_key="LEAK-LIVE-KEY",
    )
    warnings = s.validate()
    assert any("LIVE" in w and "PAPER" in w for w in warnings), \
        "PAPER 모드에 LIVE 키가 있으면 경고해야 함"


def test_validate_warns_live_keys_in_simulation_mode():
    s = Settings(
        trading_mode=TradingMode.SIMULATION,
        admin_token="strong",
        upbit_access_key="LIVE-KEY",
    )
    warnings = s.validate()
    assert any("LIVE" in w and "SIMULATION" in w for w in warnings)


def test_validate_no_warning_for_sandbox_keys_in_paper_mode():
    """sandbox 키만 채워져 있으면 PAPER 모드에서 OK (footgun 아님)."""
    s = Settings(
        trading_mode=TradingMode.PAPER,
        admin_token="strong",
        okx_api_key_sandbox="DEMO-KEY",
    )
    warnings = s.validate()
    # LIVE 키 관련 경고는 없어야 함
    assert not any("LIVE" in w and "PAPER" in w for w in warnings)


def test_validate_no_live_key_warning_in_live_mode():
    """LIVE 모드에 LIVE 키 있는 건 정상."""
    s = Settings(
        trading_mode=TradingMode.LIVE_MANUAL_APPROVAL,
        enable_live_trading=True,
        admin_token="strong",
        okx_api_key="LIVE-KEY",
    )
    warnings = s.validate()
    assert not any("LIVE" in w and "PAPER" in w for w in warnings)


def test_validate_warns_when_both_live_and_sandbox_okx_keys_present():
    s = Settings(
        trading_mode=TradingMode.LIVE_MANUAL_APPROVAL,
        enable_live_trading=True,
        admin_token="strong",
        okx_api_key="LIVE",
        okx_api_key_sandbox="DEMO",
    )
    warnings = s.validate()
    assert any("OKX_API_KEY" in w and "SANDBOX" in w for w in warnings), \
        "LIVE/SANDBOX 키 동시 존재 시 경고해야 함"


def test_validate_clean_when_only_one_okx_key_set():
    """둘 중 하나만 채워져 있으면 OKX 동시 경고는 없음."""
    s = Settings(
        trading_mode=TradingMode.PAPER,
        admin_token="strong",
        okx_api_key_sandbox="DEMO",
    )
    warnings = s.validate()
    assert not any("OKX_API_KEY" in w and "SANDBOX" in w for w in warnings)


# ── 5. .env.example 에 sandbox 변수 명시 ─────────────────────────

def test_env_example_lists_sandbox_keys():
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    for key in ("OKX_API_KEY_SANDBOX", "OKX_API_SECRET_SANDBOX",
                "OKX_API_PASSWORD_SANDBOX",
                "BINANCE_API_KEY_SANDBOX", "BINANCE_API_SECRET_SANDBOX"):
        assert key in text, f".env.example 에 {key} 누락"


def test_env_example_sandbox_keys_have_no_filled_values():
    """.env.example 의 모든 SANDBOX 변수도 값이 비어있어야 함."""
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    for line in text.splitlines():
        m = re.match(r"^([A-Z][A-Z0-9_]*_SANDBOX)=(.*)$", line.strip())
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        assert val == "", f".env.example 의 {key} 값이 비어있어야 함 (현재 {val!r})"


def test_env_example_references_sandbox_policy_doc():
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "sandbox" in text.lower()
    assert "#28" in text


# ── 6. 정합성 ────────────────────────────────────────────────────

def test_policy_doc_references_existing_modules():
    text = POLICY.read_text(encoding="utf-8")
    for ref in ("backend/app/brokers/base.py",
                "backend/app/core/config.py",
                "docs/api_key_policy.md"):
        assert ref in text, f"sandbox_paper_keys.md 가 {ref} 를 인용해야 함"


def test_policy_doc_references_test_file():
    """본 테스트 파일 자체를 정책이 인용해야 한다 (drift 방지)."""
    text = POLICY.read_text(encoding="utf-8")
    assert "test_sandbox_paper_keys.py" in text


def test_api_key_policy_links_to_sandbox_doc():
    """#27 (api_key_policy) 가 #28 (sandbox_paper_keys) 를 참조."""
    text = (REPO_ROOT / "docs" / "api_key_policy.md").read_text(encoding="utf-8")
    assert "Sandbox/Paper Keys 분리 정책" in text or "sandbox_paper_keys" in text.lower() \
        or "#28" in text
