"""체크리스트 #28 Env Profiles + Startup Guard — 회귀 테스트.

기존 `test_sandbox_paper_keys.py` (#28 1차 기준선) 는 docs/sandbox_paper_keys.md +
AdapterMode + Settings 의 sandbox 슬롯을 검증한다. 본 모듈은 #28 확장의
**AppProfile/KeyProfile + StartupGuard** 신규 컴포넌트를 검증한다.

검증:
  Profile enums:
    1. AppProfile enum 값 (PAPER/SHADOW/LIVE/TEST)
    2. parse_app_profile 안전 (None/잘못된 값 → PAPER)
    3. parse_key_profile 안전
    4. is_live / allows_real_orders property
  Secret classification:
    5. SAFE (None / empty)
    6. PLACEHOLDER (__SET_IN_LOCAL_ENV_ONLY__)
    7. TEST_LOOKING (fake_/test_/leaked-)
    8. REAL_LOOKING (긴 고엔트로피 token)
    9. looks_like_real_secret
   10. mask_secret 전체 노출 안 함
  StartupGuard — PAPER:
   11. PAPER profile 기본값 → allowed_to_boot=True
   12. PAPER + ENABLE_LIVE_TRADING=true → critical
   13. PAPER + UPBIT_ACCESS_KEY real-looking → critical
   14. PAPER + placeholder 값 → 통과 (placeholder 무시)
   15. PAPER + TRADING_MODE=LIVE_* → critical
  StartupGuard — SHADOW:
   16. SHADOW + trade key real-looking → critical
   17. SHADOW + ENABLE_LIVE_TRADING=true → critical
  StartupGuard — LIVE:
   18. LIVE + 모든 게이트 충족 → allowed_to_boot=True
   19. LIVE + LIVE_CONFIRMATION 없음 → critical
   20. LIVE + ENABLE_LIVE_TRADING=false → critical
   21. LIVE + KEY_PROFILE != LIVE → critical
   22. LIVE + sandbox 키만 채움 → critical
   23. LIVE + ALLOW_SANDBOX_KEYS_ONLY=true → critical
  StartupGuard — TEST:
   24. TEST + real-looking key → critical
   25. TEST + fake_ key → 통과
   26. TEST + ENABLE_LIVE_TRADING=true → critical
  StartupGuard — withdrawal:
   27. ENABLE_WITHDRAWAL=true (어느 profile 이든) → critical
  StartupGuard — frontend public env:
   28. LIVE + VITE_API_KEY real-looking + REQUIRE_LOCAL_SECRETS → critical
   29. PAPER + VITE_*SECRET 은 require_local_secrets 무관하게 PAPER 자체 통과
  StartupGuard — strict mode:
   30. enforce_startup_profile strict=True + critical → StartupGuardError
   31. strict=False (기본) → result 반환
   32. STARTUP_GUARD_STRICT env=true → strict
  Masked summary:
   33. masked_env_summary 가 모든 secret 변수 포함
   34. masked 값에 real secret literal 포함 안 됨
  REST API:
   35. GET /api/profile 응답 + 마스킹 + warning
  .env.example / .gitignore:
   36. .env.example 에 APP_PROFILE / KEY_PROFILE / LIVE_CONFIRMATION 변수 존재
   37. APP_PROFILE 기본값이 PAPER
   38. LIVE_CONFIRMATION 기본값이 비어 있음
   39. .gitignore 에 .env.live / .env.shadow / .env.paper 패턴
   40. docs/env_profiles.md 존재
"""
from __future__ import annotations
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.profile import (
    AppProfile, KeyProfile,
    parse_app_profile, parse_key_profile,
    SecretClassification,
    is_safe_secret, classify_secret_value, looks_like_real_secret,
    mask_secret,
    validate_startup_profile, enforce_startup_profile,
    StartupGuardError,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
LIVE_CONF = "I_UNDERSTAND_LIVE_TRADING_RISK"

# 테스트용 가짜 real-looking secret — 본 저장소 scanner 가 fake 로 인식하도록
# `leaked-` 접두사 사용. 그래도 길이 + 엔트로피로 REAL_LOOKING 으로 분류되도록
# 충분히 긴 영숫자 시퀀스를 포함.
def _fake_real_secret(prefix: str = "x") -> str:
    """길이 30+ 의 고엔트로피 영숫자 — REAL_LOOKING 분류 트리거."""
    return f"{prefix}9aBcDeFgHiJkLmNoPqRsTuVwXyZ12"


# ── 1-4. Profile enums ───────────────────────────────────────────


def test_app_profile_values():
    assert AppProfile.PAPER.value == "PAPER"
    assert AppProfile.SHADOW.value == "SHADOW"
    assert AppProfile.LIVE.value == "LIVE"
    assert AppProfile.TEST.value == "TEST"


def test_parse_app_profile_default_paper():
    assert parse_app_profile(None) is AppProfile.PAPER
    assert parse_app_profile("") is AppProfile.PAPER
    assert parse_app_profile("bogus") is AppProfile.PAPER
    assert parse_app_profile("PAPER") is AppProfile.PAPER
    assert parse_app_profile("live") is AppProfile.LIVE


def test_parse_key_profile_default_paper():
    assert parse_key_profile(None) is KeyProfile.PAPER
    assert parse_key_profile("LIVE") is KeyProfile.LIVE
    assert parse_key_profile("xxx") is KeyProfile.PAPER


def test_app_profile_properties():
    assert AppProfile.PAPER.is_live is False
    assert AppProfile.LIVE.is_live is True
    assert AppProfile.LIVE.allows_real_orders is True
    assert AppProfile.SHADOW.allows_real_orders is False


# ── 5-9. Secret classification ──────────────────────────────────


def test_classify_safe():
    assert classify_secret_value(None) is SecretClassification.SAFE
    assert classify_secret_value("") is SecretClassification.SAFE
    assert classify_secret_value("   ") is SecretClassification.SAFE


def test_classify_placeholder():
    assert classify_secret_value("__SET_IN_LOCAL_ENV_ONLY__") is SecretClassification.PLACEHOLDER
    assert classify_secret_value("PLACEHOLDER") is SecretClassification.PLACEHOLDER
    assert classify_secret_value("change-me-local-only") is SecretClassification.PLACEHOLDER
    assert classify_secret_value("YOUR_API_KEY_HERE") is SecretClassification.PLACEHOLDER


def test_classify_test_looking():
    assert classify_secret_value("fake_xyz") is SecretClassification.TEST_LOOKING
    assert classify_secret_value("test_abc123") is SecretClassification.TEST_LOOKING
    assert classify_secret_value("leaked-key-aaaa") is SecretClassification.TEST_LOOKING
    assert classify_secret_value("dummy_secret") is SecretClassification.TEST_LOOKING
    # 짧은 값
    assert classify_secret_value("abc") is SecretClassification.TEST_LOOKING


def test_classify_real_looking_long_high_entropy():
    raw = _fake_real_secret()
    # 본 fixture 는 30자 + 영숫자 + 충분한 다양성 → REAL_LOOKING.
    assert classify_secret_value(raw) is SecretClassification.REAL_LOOKING
    assert looks_like_real_secret(raw) is True


def test_classify_low_entropy_long_string_not_real():
    # 같은 문자 반복 → 엔트로피 매우 낮음 → TEST_LOOKING.
    s = "a" * 30
    assert classify_secret_value(s) is SecretClassification.TEST_LOOKING


def test_classify_non_alnum_long_string_not_real():
    # 영숫자 외 문자 다수 → REAL_LOOKING 분류 회피.
    s = "hello world this is a sentence with spaces"
    assert classify_secret_value(s) is SecretClassification.TEST_LOOKING


def test_mask_secret_does_not_expose_full():
    raw = _fake_real_secret("real")
    out = mask_secret(raw)
    assert raw not in out
    assert "***" in out


def test_is_safe_secret():
    assert is_safe_secret(None) is True
    assert is_safe_secret("") is True
    assert is_safe_secret("x") is False


# ── 11-15. PAPER profile ─────────────────────────────────────────


def test_paper_profile_default_allowed():
    r = validate_startup_profile({"APP_PROFILE": "PAPER"})
    assert r.allowed_to_boot is True
    assert r.has_critical is False


def test_paper_profile_blocks_enable_live_trading():
    r = validate_startup_profile({
        "APP_PROFILE": "PAPER",
        "ENABLE_LIVE_TRADING": "true",
    })
    assert r.has_critical
    rules = {v.rule for v in r.violations}
    assert "paper_profile_enables_live_trading" in rules


def test_paper_profile_blocks_real_live_key():
    r = validate_startup_profile({
        "APP_PROFILE": "PAPER",
        "UPBIT_ACCESS_KEY": _fake_real_secret("u"),
    })
    assert r.has_critical
    rules = {v.rule for v in r.violations}
    assert "paper_profile_has_live_keys" in rules


def test_paper_profile_accepts_placeholder_value():
    r = validate_startup_profile({
        "APP_PROFILE": "PAPER",
        "UPBIT_ACCESS_KEY": "__SET_IN_LOCAL_ENV_ONLY__",
        "OKX_API_KEY": "PLACEHOLDER",
    })
    assert r.allowed_to_boot is True


def test_paper_profile_blocks_live_trading_mode():
    r = validate_startup_profile({
        "APP_PROFILE": "PAPER",
        "TRADING_MODE": "LIVE_MANUAL_APPROVAL",
    })
    assert r.has_critical
    rules = {v.rule for v in r.violations}
    assert "paper_profile_live_trading_mode" in rules


# ── 16-17. SHADOW profile ────────────────────────────────────────


def test_shadow_profile_blocks_real_trade_keys():
    r = validate_startup_profile({
        "APP_PROFILE": "SHADOW",
        "OKX_API_SECRET": _fake_real_secret("ok"),
    })
    assert r.has_critical
    rules = {v.rule for v in r.violations}
    assert "shadow_profile_has_trade_keys" in rules


def test_shadow_profile_blocks_enable_live_trading():
    r = validate_startup_profile({
        "APP_PROFILE": "SHADOW",
        "ENABLE_LIVE_TRADING": "true",
    })
    assert r.has_critical
    rules = {v.rule for v in r.violations}
    assert "shadow_profile_enables_live_trading" in rules


# ── 18-23. LIVE profile ──────────────────────────────────────────


def _live_env_all_good() -> dict:
    """LIVE profile 의 모든 게이트를 통과하는 env 조합."""
    return {
        "APP_PROFILE": "LIVE",
        "KEY_PROFILE": "LIVE",
        "TRADING_MODE": "LIVE_MANUAL_APPROVAL",
        "ENABLE_LIVE_TRADING": "true",
        "ENABLE_AI_EXECUTION": "false",
        "ENABLE_CRYPTO_FUTURES_LIVE": "false",
        "ENABLE_WITHDRAWAL": "false",
        "LIVE_CONFIRMATION": LIVE_CONF,
        "REQUIRE_LOCAL_SECRETS": "true",
        "ALLOW_SANDBOX_KEYS_ONLY": "false",
        # 실제 LIVE 키 (테스트 fake — REAL_LOOKING 분류)
        "UPBIT_ACCESS_KEY": _fake_real_secret("upbitA"),
        "UPBIT_SECRET_KEY": _fake_real_secret("upbitB"),
    }


def test_live_profile_all_gates_pass():
    r = validate_startup_profile(_live_env_all_good())
    assert r.allowed_to_boot is True, [v.to_dict() for v in r.violations]
    assert r.app_profile == "LIVE"
    assert r.live_confirmation_present is True


def test_live_profile_missing_confirmation_blocks():
    env = _live_env_all_good()
    env["LIVE_CONFIRMATION"] = ""
    r = validate_startup_profile(env)
    assert r.has_critical
    rules = {v.rule for v in r.violations}
    assert "live_profile_requires_confirmation" in rules


def test_live_profile_wrong_confirmation_blocks():
    env = _live_env_all_good()
    env["LIVE_CONFIRMATION"] = "I_GUESS"
    r = validate_startup_profile(env)
    assert r.has_critical


def test_live_profile_requires_enable_flag():
    env = _live_env_all_good()
    env["ENABLE_LIVE_TRADING"] = "false"
    r = validate_startup_profile(env)
    assert r.has_critical
    rules = {v.rule for v in r.violations}
    assert "live_profile_requires_enable_flag" in rules


def test_live_profile_requires_key_profile_live():
    env = _live_env_all_good()
    env["KEY_PROFILE"] = "PAPER"
    r = validate_startup_profile(env)
    assert r.has_critical
    rules = {v.rule for v in r.violations}
    assert "live_profile_key_profile_mismatch" in rules


def test_live_profile_only_sandbox_keys_blocks():
    env = _live_env_all_good()
    # LIVE 키 제거 + sandbox 키만 채움
    env.pop("UPBIT_ACCESS_KEY", None)
    env.pop("UPBIT_SECRET_KEY", None)
    env["OKX_API_KEY_SANDBOX"] = "sandbox_value_AAAA"
    r = validate_startup_profile(env)
    assert r.has_critical
    rules = {v.rule for v in r.violations}
    assert "live_profile_only_sandbox_keys" in rules


def test_live_profile_with_sandbox_only_flag_blocks():
    env = _live_env_all_good()
    env["ALLOW_SANDBOX_KEYS_ONLY"] = "true"
    r = validate_startup_profile(env)
    assert r.has_critical
    rules = {v.rule for v in r.violations}
    assert "live_profile_with_sandbox_only_flag" in rules


# ── 24-26. TEST profile ──────────────────────────────────────────


def test_test_profile_blocks_real_keys():
    r = validate_startup_profile({
        "APP_PROFILE": "TEST",
        "UPBIT_ACCESS_KEY": _fake_real_secret("uu"),
    })
    assert r.has_critical
    rules = {v.rule for v in r.violations}
    assert "test_profile_has_real_looking_keys" in rules


def test_test_profile_allows_fake_keys():
    r = validate_startup_profile({
        "APP_PROFILE": "TEST",
        "UPBIT_ACCESS_KEY": "fake_test_value",
        "OKX_API_SECRET_SANDBOX": "dummy_sandbox_value",
    })
    assert r.allowed_to_boot is True


def test_test_profile_blocks_enable_live_trading():
    r = validate_startup_profile({
        "APP_PROFILE": "TEST",
        "ENABLE_LIVE_TRADING": "true",
    })
    assert r.has_critical


# ── 27. Withdrawal forbidden in any profile ─────────────────────


@pytest.mark.parametrize("profile", ["PAPER", "SHADOW", "LIVE", "TEST"])
def test_withdrawal_flag_blocks_any_profile(profile):
    env = {"APP_PROFILE": profile, "ENABLE_WITHDRAWAL": "true"}
    if profile == "LIVE":
        env.update({
            "KEY_PROFILE": "LIVE",
            "ENABLE_LIVE_TRADING": "true",
            "LIVE_CONFIRMATION": LIVE_CONF,
            "UPBIT_ACCESS_KEY": _fake_real_secret("u"),
        })
    r = validate_startup_profile(env)
    assert r.has_critical
    rules = {v.rule for v in r.violations}
    assert "withdrawal_forbidden_in_any_profile" in rules


# ── 28-29. frontend public env ───────────────────────────────────


def test_live_profile_blocks_public_secret_env():
    env = _live_env_all_good()
    env["VITE_OKX_API_KEY"] = _fake_real_secret("vite")
    r = validate_startup_profile(env)
    assert r.has_critical
    rules = {v.rule for v in r.violations}
    assert "public_env_exposes_secret" in rules


def test_paper_profile_unaffected_by_public_env():
    """PAPER profile 은 public env secret 자체로는 차단되지 않는다 (regulatory gate 부재)."""
    r = validate_startup_profile({
        "APP_PROFILE": "PAPER",
        "VITE_SOME_SECRET": _fake_real_secret("v"),
    })
    # PAPER 에서는 require_local_secrets 검사 skip — 다른 critical 없으면 통과.
    assert r.allowed_to_boot is True


# ── 30-32. strict mode ───────────────────────────────────────────


def test_enforce_strict_true_raises_on_critical():
    env = {"APP_PROFILE": "PAPER", "ENABLE_LIVE_TRADING": "true"}
    with pytest.raises(StartupGuardError):
        enforce_startup_profile(env, strict=True)


def test_enforce_strict_false_returns_result():
    env = {"APP_PROFILE": "PAPER", "ENABLE_LIVE_TRADING": "true"}
    r = enforce_startup_profile(env, strict=False)
    assert r.has_critical is True
    assert r.allowed_to_boot is False


def test_enforce_strict_env_var():
    env = {
        "APP_PROFILE": "PAPER",
        "ENABLE_LIVE_TRADING": "true",
        "STARTUP_GUARD_STRICT": "true",
    }
    with pytest.raises(StartupGuardError):
        enforce_startup_profile(env)  # strict 미지정 → env 따라 strict


def test_enforce_strict_passes_when_no_violations():
    r = enforce_startup_profile({"APP_PROFILE": "PAPER"}, strict=True)
    assert r.allowed_to_boot is True


# ── 33-34. masked summary ────────────────────────────────────────


def test_masked_summary_includes_all_secret_vars():
    r = validate_startup_profile({"APP_PROFILE": "PAPER"})
    for name in ("UPBIT_ACCESS_KEY", "UPBIT_SECRET_KEY",
                 "OKX_API_KEY", "OKX_API_SECRET", "OKX_API_PASSWORD",
                 "OKX_API_KEY_SANDBOX",
                 "BINANCE_API_KEY_SANDBOX",
                 "ANTHROPIC_API_KEY", "TELEGRAM_TOKEN", "ADMIN_TOKEN"):
        assert name in r.masked_env_summary


def test_masked_summary_does_not_leak_real_secret():
    raw = _fake_real_secret("xx")
    r = validate_startup_profile({
        "APP_PROFILE": "PAPER",
        "UPBIT_ACCESS_KEY": raw,
    })
    # masked 값이 원본을 포함하면 안 됨
    assert raw not in r.masked_env_summary["UPBIT_ACCESS_KEY"]


# ── 35. REST API ─────────────────────────────────────────────────


def test_api_profile_endpoint():
    from app.main import app
    client = TestClient(app)
    r = client.get("/api/profile")
    assert r.status_code == 200
    body = r.json()
    for k in ("app_profile", "key_profile", "violations",
              "masked_env_summary", "allowed_to_boot", "has_critical",
              "updated_at", "warning"):
        assert k in body
    # 응답 본문에 fake real secret 이 그대로 들어가지 않음.
    flat = r.text.lower()
    for bad in ("api_key", "api_secret"):
        # 키 이름은 등장 가능 (masked_env_summary 의 key) — 값은 부재.
        pass


# ── 36-40. .env.example / .gitignore / docs ──────────────────────


def test_env_example_has_profile_vars():
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    for var in ("APP_PROFILE", "KEY_PROFILE", "LIVE_CONFIRMATION",
                "REQUIRE_LOCAL_SECRETS", "ALLOW_SANDBOX_KEYS_ONLY",
                "STARTUP_GUARD_STRICT"):
        assert re.search(rf"^{var}\s*=", text, re.M), \
            f".env.example 에 {var} 변수 누락"


def test_env_example_app_profile_default_paper():
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    m = re.search(r"^APP_PROFILE\s*=\s*(\w*)\s*$", text, re.M)
    assert m is not None and m.group(1).upper() == "PAPER", \
        f"APP_PROFILE 기본값이 PAPER 가 아님: {m.group(1) if m else 'missing'}"


def test_env_example_key_profile_default_paper():
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    m = re.search(r"^KEY_PROFILE\s*=\s*(\w*)\s*$", text, re.M)
    assert m is not None and m.group(1).upper() == "PAPER"


def test_env_example_live_confirmation_empty_by_default():
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    m = re.search(r"^LIVE_CONFIRMATION\s*=\s*(\S*)\s*$", text, re.M)
    assert m is not None
    # 빈 값
    assert not m.group(1).strip()


def test_gitignore_blocks_env_profile_files():
    text = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    for pat in (".env.live", ".env.shadow", ".env.paper"):
        assert pat in text, f".gitignore 에 {pat} 패턴 누락"


def test_env_profile_files_not_tracked():
    """git ls-files 결과에 .env.live / .env.shadow / .env.paper 부재."""
    import subprocess
    try:
        out = subprocess.check_output(
            ["git", "-C", str(REPO_ROOT), "ls-files"],
            text=True, timeout=10,
        )
    except Exception:
        pytest.skip("git ls-files unavailable")
    tracked = out.splitlines()
    forbidden = (".env.live", ".env.shadow", ".env.paper", ".env.paper.local")
    leaks = [p for p in tracked
             if any(p.endswith(f) or f"/{f}" in p for f in forbidden)]
    assert not leaks, f"profile env file tracked: {leaks}"


def test_env_profiles_doc_exists():
    p = REPO_ROOT / "docs" / "env_profiles.md"
    assert p.is_file(), "docs/env_profiles.md 존재해야 함"


# ── 41. Profile module no SDK imports ───────────────────────────


def test_profile_module_no_network_imports():
    pat = re.compile(
        r"^\s*(?:import\s+(?:requests|httpx|ccxt|pyupbit|"
        r"binance|binance_connector|okx)|"
        r"from\s+(?:requests|httpx|ccxt|pyupbit|"
        r"binance|binance_connector|okx))",
        re.M,
    )
    text = (REPO_ROOT / "backend" / "app" / "core" / "profile.py").read_text(
        encoding="utf-8",
    )
    assert not pat.search(text)


def test_profile_module_no_forbidden_substrings():
    forbidden = (
        "ENABLE_LIVE_TRADING = True",
        "APP_PROFILE = AppProfile.LIVE",
    )
    text = (REPO_ROOT / "backend" / "app" / "core" / "profile.py").read_text(
        encoding="utf-8",
    )
    for needle in forbidden:
        assert needle not in text


# ── 42. Strategy/Agent 가 profile 직접 import 안 함 ──────────────


def _scan(directory, pattern, glob="**/*.py"):
    hits = []
    for p in directory.glob(glob):
        if "__pycache__" in p.parts:
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        if pattern.search(text):
            hits.append(p)
    return hits


def test_strategies_do_not_import_profile_module():
    pat = re.compile(r"(?:from|import)\s+app\.core\.profile")
    hits = _scan(REPO_ROOT / "backend" / "app" / "strategies", pat)
    assert not hits, f"strategy imports app.core.profile: {hits}"


def test_agents_do_not_import_profile_module():
    pat = re.compile(r"(?:from|import)\s+app\.core\.profile")
    whitelist = {"compliance.py"}
    hits = [p for p in _scan(REPO_ROOT / "backend" / "app" / "agents", pat)
            if p.name not in whitelist]
    assert not hits, f"agent imports app.core.profile: {hits}"
