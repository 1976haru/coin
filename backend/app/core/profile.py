"""Env Profile + Startup Guard — 체크리스트 #28.

PAPER / SHADOW / LIVE / TEST 프로파일을 명시적으로 분리하고, 프로파일에 맞지 않는
키/플래그 조합이 감지되면 startup 단계에서 차단(또는 경고)한다.

기존 `TradingMode` (#3, app/core/modes.py) 와의 관계:
  - `TradingMode` 는 6단계 운용 모드 (SIMULATION/PAPER/LIVE_SHADOW/LIVE_MANUAL_APPROVAL
    /LIVE_AI_ASSIST/LIVE_AI_EXECUTION) — *전략 실행 정책*.
  - `AppProfile` 은 4단계 *환경/키 프로파일* (PAPER/SHADOW/LIVE/TEST) — *어떤 키를
    써도 되는가*. 둘은 직교 — 같은 PAPER profile 에서 다양한 TradingMode 가능.

기존 `AdapterMode` (#20, app/brokers/base.py) 와의 관계:
  - `AdapterMode` = READ_ONLY/PAPER/SANDBOX/LIVE — *어댑터별* 동작 등급.
  - `KeyProfile` = PAPER/SHADOW/LIVE/TEST — *전역* 키 슬롯 분류.

설계 원칙 (CLAUDE.md §2.1 / §2.6):
  - 기본은 PAPER. LIVE 는 명시적 confirmation 없이 시작 불가.
  - PAPER + LIVE 키 감지 → 차단.
  - LIVE + sandbox-only 키 → 차단.
  - LIVE + LIVE_CONFIRMATION 미충족 → 차단.
  - 출금 권한 flag (`ENABLE_WITHDRAWAL`) 가 어떤 경로로든 True → 차단 (#27 정책).
  - secret 값은 본 모듈의 어떤 결과/repr 에도 평문으로 노출되지 않는다 (mask_secret).
"""
from __future__ import annotations
import math
import os
import re
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Iterable, Mapping


# ── Profile 정의 ──────────────────────────────────────────────────


class AppProfile(str, Enum):
    """앱 전역 환경 프로파일."""

    PAPER  = "PAPER"     # 기본 — Mock/Paper broker, secret 불필요
    SHADOW = "SHADOW"    # read-only 실시세 + paper decision 검증
    LIVE   = "LIVE"      # 실거래 (startup guard 통과 후에만)
    TEST   = "TEST"      # CI/pytest 전용

    @property
    def is_live(self) -> bool:
        return self is AppProfile.LIVE

    @property
    def allows_real_orders(self) -> bool:
        return self is AppProfile.LIVE


class KeyProfile(str, Enum):
    """키 슬롯 프로파일 — 어떤 키를 쓰고 있는지."""

    PAPER  = "PAPER"     # 키 없음 (Mock/Paper)
    SHADOW = "SHADOW"    # read-only 키만
    LIVE   = "LIVE"      # LIVE 키
    TEST   = "TEST"      # 명시적 fake/test 값

    @property
    def is_live(self) -> bool:
        return self is KeyProfile.LIVE


_VALID_APP_PROFILES = {p.value for p in AppProfile}
_VALID_KEY_PROFILES = {p.value for p in KeyProfile}


def parse_app_profile(value: str | None) -> AppProfile:
    """안전한 enum 변환. 미지정/잘못된 값 → ``PAPER`` (default deny)."""
    if not value:
        return AppProfile.PAPER
    v = str(value).strip().upper()
    if v in _VALID_APP_PROFILES:
        return AppProfile(v)
    return AppProfile.PAPER


def parse_key_profile(value: str | None) -> KeyProfile:
    if not value:
        return KeyProfile.PAPER
    v = str(value).strip().upper()
    if v in _VALID_KEY_PROFILES:
        return KeyProfile(v)
    return KeyProfile.PAPER


# ── Secret classification / masking ──────────────────────────────


class SecretClassification(str, Enum):
    SAFE         = "SAFE"          # None / empty
    PLACEHOLDER  = "PLACEHOLDER"   # __SET_IN_LOCAL_ENV_ONLY__ 등
    TEST_LOOKING = "TEST_LOOKING"  # fake_/test_/dummy_ 접두사 등
    REAL_LOOKING = "REAL_LOOKING"  # 고엔트로피 long token


# placeholder 인식 패턴.
_PLACEHOLDER_TOKENS: tuple[str, ...] = (
    "__SET_IN_LOCAL_ENV_ONLY__",
    "<set in local env>",
    "<unset>",
    "PLACEHOLDER",
    "CHANGE_ME",
    "change-me",
    "change-me-local-only",
    "your_",
    "YOUR_",
    "<your_",
)

# 테스트 fixture 식별자.
_TEST_PREFIXES: tuple[str, ...] = (
    "fake_", "fake-", "test_", "test-",
    "dummy_", "dummy-",
    "mock_", "mock-",
    "example_", "example-",
    "leaked-",        # 본 저장소 테스트 fixture
)


def is_safe_secret(value: Any) -> bool:
    """SAFE — None / empty 만."""
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def _shannon_entropy_bits_per_char(s: str) -> float:
    if not s:
        return 0.0
    freq: dict[str, int] = {}
    for ch in s:
        freq[ch] = freq.get(ch, 0) + 1
    n = len(s)
    entropy = 0.0
    for c in freq.values():
        p = c / n
        entropy -= p * math.log2(p)
    return entropy


def classify_secret_value(value: Any) -> SecretClassification:
    """secret 값의 분류. 본 함수는 *값을 출력하지 않는다*."""
    if value is None:
        return SecretClassification.SAFE
    if not isinstance(value, str):
        return SecretClassification.SAFE
    s = value.strip()
    if not s:
        return SecretClassification.SAFE
    s_lower = s.lower()
    # placeholder 명시 토큰
    if any(tok.lower() in s_lower for tok in _PLACEHOLDER_TOKENS):
        return SecretClassification.PLACEHOLDER
    # test/fake 접두사
    for pfx in _TEST_PREFIXES:
        if s_lower.startswith(pfx):
            return SecretClassification.TEST_LOOKING
    # 짧은 값 (≤ 19자) 은 real-looking 으로 보지 않는다.
    if len(s) < 20:
        return SecretClassification.TEST_LOOKING
    # 영숫자/하이픈/언더바 비율 + 엔트로피로 real-looking 추정.
    if not re.fullmatch(r"[A-Za-z0-9_+\-/=]+", s):
        return SecretClassification.TEST_LOOKING
    entropy = _shannon_entropy_bits_per_char(s)
    if entropy >= 3.5:
        return SecretClassification.REAL_LOOKING
    return SecretClassification.TEST_LOOKING


def looks_like_real_secret(value: Any) -> bool:
    """REAL_LOOKING 분류이면 True."""
    return classify_secret_value(value) is SecretClassification.REAL_LOOKING


# mask_secret 은 #27 의 secret_masking 모듈을 재export — 중복 정의 회피.
from app.audit.secret_masking import mask_secret  # noqa: E402


# ── StartupGuard ─────────────────────────────────────────────────


_DEFAULT_LIVE_CONFIRMATION_VALUE = "I_UNDERSTAND_LIVE_TRADING_RISK"


@dataclass(frozen=True)
class GuardViolation:
    """단일 검증 위반."""

    rule: str
    severity: str          # "critical" | "warning"
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StartupGuardResult:
    """startup guard 결과 — 위반 목록 + boot 허용 여부."""

    app_profile: str
    key_profile: str
    enable_live_trading: bool
    enable_ai_execution: bool
    enable_crypto_futures_live: bool
    require_local_secrets: bool
    allow_sandbox_keys_only: bool
    live_confirmation_present: bool
    violations: tuple[GuardViolation, ...]
    masked_env_summary: dict[str, str]

    @property
    def has_critical(self) -> bool:
        return any(v.severity == "critical" for v in self.violations)

    @property
    def allowed_to_boot(self) -> bool:
        """critical 위반이 없으면 부팅 허용."""
        return not self.has_critical

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["violations"] = [v.to_dict() for v in self.violations]
        d["has_critical"] = self.has_critical
        d["allowed_to_boot"] = self.allowed_to_boot
        return d


# 거래소 LIVE secret 변수명 — `Settings` 와 일치.
_LIVE_SECRET_VARS: tuple[str, ...] = (
    "UPBIT_ACCESS_KEY", "UPBIT_SECRET_KEY",
    "OKX_API_KEY", "OKX_API_SECRET", "OKX_API_PASSWORD",
)

# Sandbox/Testnet secret 변수명.
_SANDBOX_SECRET_VARS: tuple[str, ...] = (
    "OKX_API_KEY_SANDBOX", "OKX_API_SECRET_SANDBOX",
    "OKX_API_PASSWORD_SANDBOX",
    "BINANCE_API_KEY_SANDBOX", "BINANCE_API_SECRET_SANDBOX",
)

# 모든 secret 변수 (mask summary 대상).
_ALL_SECRET_VARS: tuple[str, ...] = (
    _LIVE_SECRET_VARS + _SANDBOX_SECRET_VARS +
    ("ANTHROPIC_API_KEY", "EXCHANGERATE_API_KEY",
     "TELEGRAM_TOKEN", "ADMIN_TOKEN")
)


def _env_bool(env: Mapping[str, str], name: str, default: bool = False) -> bool:
    v = env.get(name)
    if v is None:
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "on"}


def _present_real_keys(env: Mapping[str, str], names: Iterable[str]) -> list[str]:
    """입력된 변수 중 REAL_LOOKING 분류 값을 가진 이름 목록."""
    out: list[str] = []
    for n in names:
        if looks_like_real_secret(env.get(n)):
            out.append(n)
    return out


def _present_any(env: Mapping[str, str], names: Iterable[str]) -> list[str]:
    """입력된 변수 중 SAFE / PLACEHOLDER 가 아닌 값(=어떤 값이라도 채워진) 이름 목록."""
    out: list[str] = []
    for n in names:
        c = classify_secret_value(env.get(n))
        if c not in (SecretClassification.SAFE, SecretClassification.PLACEHOLDER):
            out.append(n)
    return out


def validate_startup_profile(
    env: Mapping[str, str] | None = None,
    *,
    expected_confirmation: str = _DEFAULT_LIVE_CONFIRMATION_VALUE,
) -> StartupGuardResult:
    """env 기반으로 profile/key 정합성을 검증.

    *순수 함수* — env 만 입력으로 받고 외부 부작용 없음. 테스트는 ``env={...}`` 로
    완전 격리 가능. 실제 호출자는 ``os.environ`` 을 그대로 넘기면 된다.
    """
    e: Mapping[str, str] = env if env is not None else os.environ

    app_profile = parse_app_profile(e.get("APP_PROFILE"))
    key_profile = parse_key_profile(e.get("KEY_PROFILE"))

    enable_live_trading = _env_bool(e, "ENABLE_LIVE_TRADING", False)
    enable_ai_execution = _env_bool(e, "ENABLE_AI_EXECUTION", False)
    enable_crypto_futures_live = _env_bool(e, "ENABLE_CRYPTO_FUTURES_LIVE", False)
    enable_withdrawal = _env_bool(e, "ENABLE_WITHDRAWAL", False)
    require_local_secrets = _env_bool(e, "REQUIRE_LOCAL_SECRETS", True)
    allow_sandbox_keys_only = _env_bool(e, "ALLOW_SANDBOX_KEYS_ONLY", False)
    live_confirmation = (e.get("LIVE_CONFIRMATION") or "").strip()
    live_confirmation_present = (
        bool(live_confirmation)
        and live_confirmation == expected_confirmation
    )

    violations: list[GuardViolation] = []

    # ── 1. 출금 권한은 어떤 profile/모드에서도 금지 ──────────────
    if enable_withdrawal:
        violations.append(GuardViolation(
            rule="withdrawal_forbidden_in_any_profile",
            severity="critical",
            reason="ENABLE_WITHDRAWAL=true 는 영구 금지 (CLAUDE.md §2.1 / docs/api_key_policy.md).",
        ))

    # ── 2. PAPER profile 에서 LIVE 키 감지 ───────────────────────
    if app_profile is AppProfile.PAPER:
        leaked = _present_real_keys(e, _LIVE_SECRET_VARS)
        if leaked:
            violations.append(GuardViolation(
                rule="paper_profile_has_live_keys",
                severity="critical",
                reason=("PAPER profile 인데 LIVE 키가 채워짐: "
                        + ", ".join(leaked)),
            ))
        if enable_live_trading:
            violations.append(GuardViolation(
                rule="paper_profile_enables_live_trading",
                severity="critical",
                reason="PAPER profile 에서 ENABLE_LIVE_TRADING=true.",
            ))

    # ── 3. SHADOW profile — trade/withdraw 키 금지, read-only 허용 ─
    if app_profile is AppProfile.SHADOW:
        leaked = _present_real_keys(e, _LIVE_SECRET_VARS)
        if leaked and not allow_sandbox_keys_only:
            # SHADOW 에서 LIVE 키가 들어오면 위험 — strict 모드면 차단.
            violations.append(GuardViolation(
                rule="shadow_profile_has_trade_keys",
                severity="critical",
                reason=("SHADOW profile 에서 trade/private LIVE 키 채워짐: "
                        + ", ".join(leaked)),
            ))
        if enable_live_trading:
            violations.append(GuardViolation(
                rule="shadow_profile_enables_live_trading",
                severity="critical",
                reason="SHADOW profile 에서 ENABLE_LIVE_TRADING=true.",
            ))

    # ── 4. LIVE profile — 모든 게이트 통과 필요 ─────────────────
    if app_profile is AppProfile.LIVE:
        if not enable_live_trading:
            violations.append(GuardViolation(
                rule="live_profile_requires_enable_flag",
                severity="critical",
                reason="LIVE profile 인데 ENABLE_LIVE_TRADING=false. 명시적 활성화 필요.",
            ))
        if not live_confirmation_present:
            violations.append(GuardViolation(
                rule="live_profile_requires_confirmation",
                severity="critical",
                reason=(f"LIVE profile 인데 LIVE_CONFIRMATION="
                        f"{expected_confirmation!r} 미설정."),
            ))
        if key_profile is not KeyProfile.LIVE:
            violations.append(GuardViolation(
                rule="live_profile_key_profile_mismatch",
                severity="critical",
                reason=(f"LIVE profile 인데 KEY_PROFILE={key_profile.value!r} "
                        "(LIVE 아님)."),
            ))
        # LIVE profile 에 sandbox-only 키만 채워지면 misconfiguration.
        live_keys_present = _present_real_keys(e, _LIVE_SECRET_VARS)
        sandbox_keys_present = _present_any(e, _SANDBOX_SECRET_VARS)
        if not live_keys_present and sandbox_keys_present:
            violations.append(GuardViolation(
                rule="live_profile_only_sandbox_keys",
                severity="critical",
                reason=("LIVE profile 인데 LIVE 키가 비어있고 sandbox 키만 채워짐: "
                        + ", ".join(sandbox_keys_present)),
            ))
        if allow_sandbox_keys_only:
            violations.append(GuardViolation(
                rule="live_profile_with_sandbox_only_flag",
                severity="critical",
                reason="LIVE profile 인데 ALLOW_SANDBOX_KEYS_ONLY=true.",
            ))

    # ── 5. TEST profile — real-looking key 차단 ─────────────────
    if app_profile is AppProfile.TEST:
        real = _present_real_keys(e, _LIVE_SECRET_VARS + _SANDBOX_SECRET_VARS)
        if real:
            violations.append(GuardViolation(
                rule="test_profile_has_real_looking_keys",
                severity="critical",
                reason=("TEST profile 인데 real-looking key 감지: "
                        + ", ".join(real)),
            ))
        if enable_live_trading:
            violations.append(GuardViolation(
                rule="test_profile_enables_live_trading",
                severity="critical",
                reason="TEST profile 에서 ENABLE_LIVE_TRADING=true.",
            ))

    # ── 6. KEY_PROFILE 과 TRADING_MODE 불일치 (경고) ─────────────
    trading_mode = (e.get("TRADING_MODE") or "").upper()
    if app_profile is AppProfile.PAPER and trading_mode.startswith("LIVE"):
        violations.append(GuardViolation(
            rule="paper_profile_live_trading_mode",
            severity="critical",
            reason=(f"PAPER profile 인데 TRADING_MODE={trading_mode!r}."),
        ))
    if app_profile is AppProfile.LIVE and trading_mode and not trading_mode.startswith("LIVE"):
        violations.append(GuardViolation(
            rule="live_profile_non_live_trading_mode",
            severity="warning",
            reason=(f"LIVE profile 에 TRADING_MODE={trading_mode!r} — 모드/프로파일 "
                    "정합성 검토."),
        ))

    # ── 7. require_local_secrets — frontend public env 검사 ──────
    # APP_PROFILE 이 LIVE/SHADOW 이고, VITE_/NEXT_PUBLIC_ 경유로 들어온 secret 류
    # 변수가 발견되면 차단.
    if app_profile in (AppProfile.LIVE, AppProfile.SHADOW) and require_local_secrets:
        leaked_public = _detect_public_secret_envs(e)
        if leaked_public:
            violations.append(GuardViolation(
                rule="public_env_exposes_secret",
                severity="critical",
                reason=("frontend public env (VITE_/NEXT_PUBLIC_) 에 secret 패턴 감지: "
                        + ", ".join(leaked_public)),
            ))

    # ── masked summary ───────────────────────────────────────────
    masked: dict[str, str] = {}
    for name in _ALL_SECRET_VARS:
        masked[name] = mask_secret(e.get(name))

    return StartupGuardResult(
        app_profile=app_profile.value,
        key_profile=key_profile.value,
        enable_live_trading=enable_live_trading,
        enable_ai_execution=enable_ai_execution,
        enable_crypto_futures_live=enable_crypto_futures_live,
        require_local_secrets=require_local_secrets,
        allow_sandbox_keys_only=allow_sandbox_keys_only,
        live_confirmation_present=live_confirmation_present,
        violations=tuple(violations),
        masked_env_summary=masked,
    )


def _detect_public_secret_envs(env: Mapping[str, str]) -> list[str]:
    out: list[str] = []
    pat = re.compile(
        r"^(VITE_|NEXT_PUBLIC_)[A-Z0-9_]*(SECRET|ACCESS_TOKEN|API_KEY|PASSPHRASE)",
        re.I,
    )
    for k, v in env.items():
        if pat.match(k or ""):
            c = classify_secret_value(v)
            if c is SecretClassification.REAL_LOOKING:
                out.append(k)
    return out


# ── strict mode startup hook ─────────────────────────────────────


class StartupGuardError(RuntimeError):
    """strict mode 에서 critical violation 발생 시 raise."""


def enforce_startup_profile(
    env: Mapping[str, str] | None = None,
    *,
    strict: bool | None = None,
    expected_confirmation: str = _DEFAULT_LIVE_CONFIRMATION_VALUE,
) -> StartupGuardResult:
    """validate + strict 모드일 때 critical 위반 시 raise.

    ``strict`` 기본은 env ``STARTUP_GUARD_STRICT`` 값. 둘 다 미지정이면 False (테스트
    안정성). 운영 배포 스크립트에서 명시적으로 strict=True 호출 권장.
    """
    e: Mapping[str, str] = env if env is not None else os.environ
    if strict is None:
        strict = _env_bool(e, "STARTUP_GUARD_STRICT", False)
    result = validate_startup_profile(e, expected_confirmation=expected_confirmation)
    if strict and result.has_critical:
        critical = [v for v in result.violations if v.severity == "critical"]
        reasons = "; ".join(v.reason for v in critical[:5])
        raise StartupGuardError(
            f"startup blocked — {len(critical)} critical violation(s): {reasons}"
        )
    return result


__all__ = (
    "AppProfile",
    "KeyProfile",
    "parse_app_profile",
    "parse_key_profile",
    "SecretClassification",
    "is_safe_secret",
    "classify_secret_value",
    "looks_like_real_secret",
    "mask_secret",
    "GuardViolation",
    "StartupGuardResult",
    "StartupGuardError",
    "validate_startup_profile",
    "enforce_startup_profile",
)
