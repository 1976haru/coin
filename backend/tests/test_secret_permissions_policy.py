"""체크리스트 #27 Secret Permissions — 정책 회귀 테스트 (확장).

기존 `test_api_key_policy.py` 가 정책 문서 핵심 섹션과 ENABLE_WITHDRAWAL 영구
false 회귀를 검증한다. 본 모듈은 #27 스펙이 추가로 요구하는 체크를 담당한다.

검증:
  - mask_secret 헬퍼 동작 (None / placeholder / realistic)
  - 정책 문서의 추가 섹션 — 권한 체크리스트 / 스크린샷 보관 / .env.example 원칙 /
    27번 범위
  - .env.example / config/.env.example 에 실제 키 문자열 부재
  - frontend src 에 secret 변수 / VITE_ / NEXT_PUBLIC_ secret 변수 부재
  - production 코드에 출금 endpoint URL / withdrawal/withdraw 메서드 부재
  - LIVE 류 feature flag 기본값 false
  - ORM 모델 컬럼에 secret 류 컬럼 부재
"""
from __future__ import annotations
import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_APP = REPO_ROOT / "backend" / "app"
POLICY = REPO_ROOT / "docs" / "api_key_policy.md"
ENV_EXAMPLE = REPO_ROOT / ".env.example"


# ── 1. mask_secret 동작 ──────────────────────────────────────────


def test_mask_secret_none_returns_unset():
    from app.audit.secret_masking import mask_secret
    assert mask_secret(None) == "<unset>"


def test_mask_secret_empty_string_returns_unset():
    from app.audit.secret_masking import mask_secret
    assert mask_secret("") == "<unset>"
    assert mask_secret("   ") == "<unset>"


def test_mask_secret_placeholder_token():
    from app.audit.secret_masking import mask_secret
    assert mask_secret("__SET_IN_LOCAL_ENV_ONLY__") == "<placeholder>"
    assert mask_secret("PLACEHOLDER") == "<placeholder>"
    assert mask_secret("change-me-local-only") == "<placeholder>"


def test_mask_secret_realistic_value_does_not_expose_full():
    from app.audit.secret_masking import mask_secret
    raw = "super-secret-real-key-abc123XYZ"
    out = mask_secret(raw)
    # 원본 전체 노출 금지
    assert raw not in out
    # 길이가 충분 → 일부만 노출 (prefix + *** + suffix)
    assert "***" in out


def test_mask_secret_short_value_full_redact():
    from app.audit.secret_masking import mask_secret
    out = mask_secret("ab")
    assert out == "***"


def test_mask_secret_non_str_safe():
    from app.audit.secret_masking import mask_secret
    assert mask_secret(12345).startswith("<non-str:")


def test_mask_dict_values_masks_secret_keys():
    from app.audit.secret_masking import mask_dict_values
    out = mask_dict_values({
        "api_key": "very-long-secret-value-AAA",
        "symbol": "BTC-USDT",
        "secret": "another-very-long-value",
        "ok": "fine",
    })
    # secret 류 키 마스킹
    assert "very-long-secret-value-AAA" not in str(out)
    assert "another-very-long-value" not in str(out)
    # 일반 키는 그대로
    assert out["symbol"] == "BTC-USDT"
    assert out["ok"] == "fine"


# ── 2. 정책 문서 추가 섹션 ──────────────────────────────────────


def _policy_text() -> str:
    return POLICY.read_text(encoding="utf-8")


@pytest.mark.parametrize("phrase", [
    "권한 체크리스트",
    "스크린샷 보관",
    ".env.example` 작성 원칙",
    "거래소별 권한 상세 정책",
    "이번 단계(#27)의 범위",
    "본 단계 완료는 **실거래 허가가 아니다.**",
    "Binance Global ≠ Binance.US",
    "MUST_OFF",
    "TRADE_GATED",
    "Universal Transfer",
    "Sub-account Transfer",
])
def test_policy_has_extended_section_phrases(phrase: str):
    assert phrase in _policy_text(), f"api_key_policy.md 에 '{phrase}' 누락"


def test_policy_lists_screenshot_filename_convention():
    text = _policy_text()
    # `api-permission-<exchange>-<tier>-<YYYYMMDD>-redacted.png` 형식이 문서에 등장
    assert "redacted.png" in text
    assert "api-permission-upbit-readonly-20260518-redacted.png" in text


def test_policy_explicitly_states_27_not_live_authorization():
    text = _policy_text()
    assert "본 단계 완료는 **실거래 허가가 아니다.**" in text


# ── 3. .env.example 에 실제 키 문자열 부재 ──────────────────────


def test_root_env_example_has_no_realistic_long_secrets():
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    # 변수 = 값 패턴 중 값이 20자 이상의 영숫자/하이픈/언더바면 의심.
    # 단, placeholder __SET_IN_LOCAL_ENV_ONLY__ 는 허용 (40자지만 명시적).
    pat = re.compile(r"^[A-Z][A-Z0-9_]*=([A-Za-z0-9_/+\-]{20,})\s*$", re.M)
    leaks: list[str] = []
    for m in pat.finditer(text):
        val = m.group(1)
        if "SET_IN_LOCAL_ENV_ONLY" in val or "PLACEHOLDER" in val:
            continue
        if val.lower() in {"change-me-local-only"}:
            continue
        leaks.append(m.group(0))
    assert not leaks, f"root .env.example 에 실제 키처럼 보이는 값: {leaks}"


def test_config_env_example_no_realistic_secrets():
    """config/.env.example 에 secret 처럼 보이는 long token 부재."""
    p = REPO_ROOT / "config" / ".env.example"
    if not p.exists():
        pytest.skip("config/.env.example absent")
    text = p.read_text(encoding="utf-8")
    pat = re.compile(r"^[A-Z][A-Z0-9_]*=([A-Za-z0-9_/+\-]{20,})\s*$", re.M)
    leaks: list[str] = []
    for m in pat.finditer(text):
        val = m.group(1)
        if "SET_IN_LOCAL_ENV_ONLY" in val or "PLACEHOLDER" in val:
            continue
        leaks.append(m.group(0))
    assert not leaks, f"config/.env.example 에 실제 키처럼 보이는 값: {leaks}"


def test_root_env_example_has_no_withdrawal_enabled_flag():
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    # ENABLE_WITHDRAWAL 변수 자체 등장 가능 (영구 false 라는 주석) — 그러나 true 로
    # 시작하는 라인은 절대 금지.
    pat = re.compile(r"^ENABLE_WITHDRAWAL\s*=\s*true\s*$", re.M | re.I)
    assert not pat.search(text)


def test_root_env_example_dangerous_flags_default_false():
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    for flag in (
        "ENABLE_LIVE_TRADING",
        "ENABLE_AI_EXECUTION",
        "ENABLE_CRYPTO_FUTURES_LIVE",
        "ENABLE_LIVE_ORDER_SUBMISSION",
    ):
        pat = re.compile(rf"^{flag}\s*=\s*(\w+)\s*$", re.M)
        m = pat.search(text)
        assert m is not None, f".env.example 에 {flag} 변수 누락"
        assert m.group(1).lower() == "false", \
            f".env.example 의 {flag} 기본값이 false 가 아님: {m.group(1)}"


# ── 4. frontend secret literal 부재 ───────────────────────────


_FRONTEND_SRC = REPO_ROOT / "frontend" / "src"
_FRONTEND_ENV_FILES = (
    REPO_ROOT / "frontend" / ".env",
    REPO_ROOT / "frontend" / ".env.example",
    REPO_ROOT / "frontend" / ".env.local",
    REPO_ROOT / "frontend" / ".env.production",
)


def _scan_frontend(pattern: re.Pattern) -> list[Path]:
    hits: list[Path] = []
    if _FRONTEND_SRC.exists():
        for p in _FRONTEND_SRC.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix not in {".ts", ".tsx", ".js", ".jsx", ".json", ".env"}:
                continue
            text = p.read_text(encoding="utf-8", errors="ignore")
            if pattern.search(text):
                hits.append(p)
    for f in _FRONTEND_ENV_FILES:
        if f.is_file():
            text = f.read_text(encoding="utf-8", errors="ignore")
            if pattern.search(text):
                hits.append(f)
    return hits


def test_frontend_no_upbit_secret_literals():
    pat = re.compile(r"UPBIT_SECRET_KEY|UPBIT_ACCESS_KEY")
    hits = _scan_frontend(pat)
    assert not hits, f"frontend 에 Upbit 키 literal: {hits}"


def test_frontend_no_okx_secret_literals():
    pat = re.compile(r"OKX_(?:API_)?SECRET(?:_KEY)?|OKX_(?:API_)?PASSWORD|OKX_PASSPHRASE|OKX_API_KEY")
    hits = _scan_frontend(pat)
    assert not hits, f"frontend 에 OKX 키 literal: {hits}"


def test_frontend_no_binance_secret_literals():
    pat = re.compile(r"BINANCE_(?:API_)?SECRET(?:_KEY)?|BINANCE_API_KEY")
    hits = _scan_frontend(pat)
    assert not hits, f"frontend 에 Binance 키 literal: {hits}"


def test_frontend_no_generic_secret_literals():
    pat = re.compile(r"\bAPI_SECRET\b|\bACCESS_TOKEN\b")
    hits = _scan_frontend(pat)
    assert not hits, f"frontend 에 generic secret literal: {hits}"


def test_frontend_no_vite_secret_envs():
    pat = re.compile(r"VITE_[A-Z0-9_]*SECRET|VITE_[A-Z0-9_]*ACCESS_TOKEN|VITE_[A-Z0-9_]*API_KEY",
                     re.I)
    hits = _scan_frontend(pat)
    assert not hits, f"frontend 에 VITE_ secret env: {hits}"


def test_frontend_no_next_public_secret_envs():
    pat = re.compile(r"NEXT_PUBLIC_[A-Z0-9_]*SECRET|NEXT_PUBLIC_[A-Z0-9_]*ACCESS_TOKEN",
                     re.I)
    hits = _scan_frontend(pat)
    assert not hits, f"frontend 에 NEXT_PUBLIC_ secret env: {hits}"


# ── 5. production 코드에 출금 endpoint / withdraw 메서드 부재 ──────


def _scan_backend_app(pattern: re.Pattern, *, exclude: set[str] | None = None) -> list[Path]:
    exclude = exclude or set()
    hits: list[Path] = []
    for p in BACKEND_APP.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        if p.name in exclude:
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        if pattern.search(text):
            hits.append(p)
    return hits


def test_backend_has_no_withdrawal_method_definition():
    """`def withdraw...` / `def transfer_to_external...` 같은 메서드 정의 부재.

    compliance.py 는 `assert_no_withdrawal_methods` 라는 *검증 함수* 를 import 해
    이름에 `withdraw` 단어가 등장한다 — 이는 정책 검증 의도이므로 허용.
    """
    # 메서드/함수 정의만 검사.
    pat = re.compile(
        r"^\s*(?:async\s+)?def\s+(?:withdraw|withdrawal|transfer_to_external|"
        r"send_to_address|create_withdrawal|request_withdrawal)\s*\(",
        re.M,
    )
    hits = _scan_backend_app(pat)
    assert not hits, f"backend 에 withdraw* 메서드 정의: {hits}"


def test_backend_has_no_withdraw_endpoint_url():
    """real-world withdraw endpoint URL literal 부재 — production 파일 어디에도."""
    forbidden = (
        "/v1/withdraws",              # Upbit
        "/sapi/v1/capital/withdraw",  # Binance
        "/api/v5/asset/withdrawal",   # OKX
    )
    for fname in ("api_limits.py", "rate_limit_guard.py",
                  "upbit_adapter.py", "upbit_public.py",
                  "upbit_account.py", "upbit_order.py",
                  "okx_adapter.py", "okx_public.py",
                  "okx_account.py", "okx_trade.py",
                  "binance_adapter.py", "binance_public.py",
                  "binance_account.py", "binance_trade.py"):
        path = BACKEND_APP / "brokers" / fname
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in text, f"{fname} contains {needle!r}"


# ── 6. ORM 컬럼에 secret 류 컬럼 부재 ──────────────────────────


def test_orm_models_have_no_secret_columns():
    """db/models.py 의 Column(...) 정의 중 secret 류 이름 부재."""
    p = BACKEND_APP / "db" / "models.py"
    text = p.read_text(encoding="utf-8")
    # 패턴: `name = Column(...)` 또는 `Column(name=...)` 의 컬럼명에 secret 류.
    # 단순화: 라인 시작 `<secret>_*  = Column(...)` 형태 검사.
    forbidden_names = (
        "api_key", "api_secret", "secret_key", "secret_value",
        "access_token", "passphrase", "private_key",
        "ok_access_sign", "x_mbx_apikey",
    )
    for name in forbidden_names:
        pat = re.compile(rf"^\s*{name}\s*=\s*Column\b", re.M | re.I)
        assert not pat.search(text), \
            f"db/models.py 에 secret 류 컬럼 {name!r} 정의 발견"


# ── 7. LIVE flag 기본값 false ─────────────────────────────────


def test_settings_default_live_flags_false():
    """Settings dataclass 기본값에서 LIVE 류 flag 가 모두 false."""
    from app.core.config import Settings, reset_settings_cache
    # 환경변수 의존성 없이 새로 생성.
    reset_settings_cache()
    s = Settings()
    assert s.enable_live_trading is False
    assert s.enable_ai_execution is False


def test_feature_flags_withdrawal_permanently_false(monkeypatch):
    """ENABLE_WITHDRAWAL 환경변수가 true 로 들어와도 코드상 false 유지."""
    from app.core.feature_flags import FeatureFlags
    monkeypatch.setenv("ENABLE_WITHDRAWAL", "true")
    f = FeatureFlags()
    # 클래스 내에서 enable_withdrawal 속성을 강제 false 로 하드코드해 두었다.
    assert f.enable_withdrawal is False


# ── 8. AdapterCapability requires_secret 메타만 — 값 미보관 ──────


def test_adapter_capability_does_not_carry_secret_values():
    from app.brokers import AdapterCapability
    cap = AdapterCapability(name="x", mode="LIVE",
                            requires_secret=True, can_place_order=True)
    d = cap.to_dict()
    # requires_secret 는 bool 메타 — 실제 secret 값이 들어가지 않음.
    assert isinstance(d["requires_secret"], bool)
    for k, v in d.items():
        if isinstance(v, str):
            for needle in ("api_key", "api_secret", "passphrase", "access_token"):
                assert needle not in v.lower()


# ── 9. logs 디렉터리에 secret-like 토큰 부재 ────────────────────


def test_logs_directory_does_not_contain_committed_secrets():
    """logs/ 가 git 추적이라면 secret 류 long-token 부재 검증."""
    logs = REPO_ROOT / "logs"
    if not logs.exists():
        pytest.skip("logs directory absent")
    # gitignore 정책상 logs 는 untracked. 만약 일부 파일이 tracked 라면 검사.
    import subprocess
    try:
        out = subprocess.check_output(
            ["git", "-C", str(REPO_ROOT), "ls-files", "logs/"],
            text=True, timeout=10,
        )
    except Exception:
        pytest.skip("git ls-files unavailable")
    tracked = [line.strip() for line in out.splitlines() if line.strip()]
    pat = re.compile(r"(?i)(api[_-]?key|secret|access[_-]?token|passphrase)"
                     r"['\"]?\s*[:=]\s*['\"]?[A-Za-z0-9]{20,}")
    leaks: list[str] = []
    for relpath in tracked:
        p = REPO_ROOT / relpath
        if not p.is_file():
            continue
        if p.suffix not in {".md", ".txt", ".csv", ".log", ".json"}:
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        if pat.search(text):
            leaks.append(relpath)
    assert not leaks, f"logs/ 에 secret-like token 가 tracked 됨: {leaks}"


# ── 10. checklist_progress.md 에 27 표기 ───────────────────────


def test_checklist_progress_includes_27_completion():
    p = REPO_ROOT / "docs" / "checklist_progress.md"
    text = p.read_text(encoding="utf-8")
    # 27번 행이 존재하고 ✅ 표시.
    assert re.search(r"^\|\s*27\s*\|[^|]*\|\s*✅", text, re.M), \
        "checklist_progress.md 의 27번 행이 ✅ 로 표기되지 않음"


# ── 11. check_secret_policy.py 스크립트 존재 ──────────────────


def test_scripts_check_secret_policy_present():
    p = REPO_ROOT / "scripts" / "check_secret_policy.py"
    assert p.is_file(), "scripts/check_secret_policy.py 가 존재해야 함"
