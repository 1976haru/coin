"""Settings — frozen dataclass, 환경변수 전용 — 체크리스트 #9 Config Layer.

설계 원칙:
  - 단일 진리 소스: 모든 런타임 설정은 환경변수에서만 읽는다 (YAML/TOML 미도입).
    이유: secret 누출 위험 최소화 + Docker/Tailscale 배포에서 12-factor 준수.
  - frozen dataclass — 런타임 변경 금지. 변경 시 프로세스 재시작.
  - Settings.summary() : secret-redacted 스냅샷 (admin UI / audit 노출용).
  - Settings.validate(): 안전 경고 리스트 반환 (insecure defaults / mode-flag mismatch).
  - .env.example 와 본 파일의 env-var 집합은 회귀 테스트로 동기화 강제.
"""
import os
from dataclasses import dataclass, field, fields
from functools import lru_cache
from .modes import TradingMode


def _bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # ── 운용 모드 ──────────────────────────────────────────────────
    trading_mode: TradingMode = TradingMode(os.getenv("TRADING_MODE", "PAPER"))

    # ── Feature Flags (기본 모두 false = 안전) ─────────────────────
    enable_live_trading:  bool = _bool("ENABLE_LIVE_TRADING")
    enable_ai_execution:  bool = _bool("ENABLE_AI_EXECUTION")
    enable_kimp_strategy: bool = _bool("ENABLE_KIMP_STRATEGY", True)   # kim_bot 핵심
    enable_ai_agents:     bool = _bool("ENABLE_AI_AGENTS")
    enable_okx:           bool = _bool("ENABLE_OKX", True)
    enable_upbit:         bool = _bool("ENABLE_UPBIT", True)

    # ── 거래 설정 ────────────────────────────────────────────────
    demo_mode: bool = _bool("DEMO_MODE", True)
    max_order_notional_usdt: float = _float("MAX_ORDER_NOTIONAL_USDT", 100.0)
    daily_loss_limit_pct:    float = _float("DAILY_LOSS_LIMIT_PCT", 2.0)
    max_open_positions:      int   = _int("MAX_OPEN_POSITIONS", 5)
    max_leverage:            float = _float("MAX_LEVERAGE", 2.0)
    re_entry_cooldown_min:   int   = _int("RE_ENTRY_COOLDOWN_MIN", 5)
    max_consecutive_losses:  int   = _int("MAX_CONSECUTIVE_LOSSES", 5)
    freshness_threshold_sec: float = _float("FRESHNESS_THRESHOLD_SEC", 5.0)

    # ── Watchlist / Universe (#14) ───────────────────────────────
    # 전체 enabled watchlist 항목의 상한. list_name 별 cap 과 함께 적용된다.
    # default_factory 로 인스턴스 생성 시점에 env 평가 — reset_settings_cache()
    # 후 monkeypatch.setenv 가 즉시 반영된다.
    watchlist_max_enabled_total: int = field(
        default_factory=lambda: _int("WATCHLIST_MAX_ENABLED_TOTAL", 100),
    )

    # ── Market Data Collector (#15) ──────────────────────────────
    # collect_all 1회 실행 시 처리할 최대 symbol 수. Watchlist cap 과는 별개로
    # 호출 시점의 추가 안전장치 (전체 시장 fallback 방지 + 비용 한도).
    market_collector_max_symbols: int = field(
        default_factory=lambda: _int("MARKET_COLLECTOR_MAX_SYMBOLS", 100),
    )

    # ── API 키 (출금 권한 금지) ──────────────────────────────────
    # LIVE 키 (#27)
    upbit_access_key: str = os.getenv("UPBIT_ACCESS_KEY", "")
    upbit_secret_key: str = os.getenv("UPBIT_SECRET_KEY", "")
    okx_api_key:      str = os.getenv("OKX_API_KEY", "")
    okx_api_secret:   str = os.getenv("OKX_API_SECRET", "")
    okx_api_password: str = os.getenv("OKX_API_PASSWORD", "")

    # Sandbox/Testnet 키 (#28) — LIVE 키와 절대 섞지 말 것.
    # docs/sandbox_paper_keys.md 참조. 본 슬롯은 SANDBOX 어댑터 구현 시 사용.
    okx_api_key_sandbox:      str = os.getenv("OKX_API_KEY_SANDBOX", "")
    okx_api_secret_sandbox:   str = os.getenv("OKX_API_SECRET_SANDBOX", "")
    okx_api_password_sandbox: str = os.getenv("OKX_API_PASSWORD_SANDBOX", "")
    binance_api_key_sandbox:    str = os.getenv("BINANCE_API_KEY_SANDBOX", "")
    binance_api_secret_sandbox: str = os.getenv("BINANCE_API_SECRET_SANDBOX", "")

    # ── AI / 텔레그램 ────────────────────────────────────────────
    anthropic_api_key:  str = os.getenv("ANTHROPIC_API_KEY", "")
    ai_model:           str = os.getenv("AI_MODEL", "claude-sonnet-4-5")
    telegram_token:     str = os.getenv("TELEGRAM_TOKEN", "")
    telegram_chat_id:   str = os.getenv("TELEGRAM_CHAT_ID", "")

    # ── 보안 ─────────────────────────────────────────────────────
    admin_token: str = os.getenv("ADMIN_TOKEN", "change-me-local-only")

    # ── 환율 ─────────────────────────────────────────────────────
    exchangerate_api_key: str   = os.getenv("EXCHANGERATE_API_KEY", "")
    usdt_krw_fallback:    float = _float("USDT_KRW_FALLBACK", 1380.0)


    # ── Introspection ────────────────────────────────────────────
    def summary(self) -> dict:
        """Secret 을 마스킹한 설정 스냅샷.

        AuditLog redaction 규칙(`app.audit.redaction.redact`)을 그대로 사용 —
        api_key/secret/passphrase/token 류는 ``***REDACTED***`` 로 치환된다.
        admin UI / audit 로그 / `/api/config/effective` 응답에 사용.
        """
        from app.audit.redaction import redact
        d: dict = {}
        for f in fields(self):
            val = getattr(self, f.name)
            if isinstance(val, TradingMode):
                val = val.value
            d[f.name] = val
        return redact(d)

    def validate(self) -> list[str]:
        """안전 경고 점검. 위반 시 경고 문자열 리스트 반환.

        반환이 비어있으면 OK. 비어있지 않더라도 프로세스를 중단시키지는 않는다 —
        호출자(예: /api/status)가 운영자에게 노출.
        """
        warnings: list[str] = []

        # admin token 기본값
        if self.admin_token in ("", "change-me-local-only"):
            warnings.append("ADMIN_TOKEN 이 기본값 — 운영 전 반드시 변경")

        live_modes = {
            TradingMode.LIVE_MANUAL_APPROVAL,
            TradingMode.LIVE_AI_ASSIST,
            TradingMode.LIVE_AI_EXECUTION,
        }

        # 모드/플래그 정합성
        if self.trading_mode in live_modes and not self.enable_live_trading:
            warnings.append(
                f"TRADING_MODE={self.trading_mode.value} 인데 ENABLE_LIVE_TRADING=false "
                "— 모드와 플래그 불일치"
            )
        if (self.trading_mode == TradingMode.LIVE_AI_EXECUTION
                and not self.enable_ai_execution):
            warnings.append(
                "TRADING_MODE=LIVE_AI_EXECUTION 인데 ENABLE_AI_EXECUTION=false"
            )

        # 비-PAPER 모드에서 보수적 한도 권장
        if self.trading_mode not in {TradingMode.PAPER, TradingMode.SIMULATION}:
            if self.max_order_notional_usdt > 1000.0:
                warnings.append(
                    f"MAX_ORDER_NOTIONAL_USDT={self.max_order_notional_usdt} 가 "
                    "비-PAPER 모드 권장 한도(1000 USDT) 초과"
                )
            if self.daily_loss_limit_pct > 5.0:
                warnings.append(
                    f"DAILY_LOSS_LIMIT_PCT={self.daily_loss_limit_pct} 가 5% 초과 "
                    "— 보수적 운영 권장"
                )
            if self.max_leverage > 3.0:
                warnings.append(
                    f"MAX_LEVERAGE={self.max_leverage}x 가 3x 초과 "
                    "— 비-PAPER 모드 권장 한도 초과"
                )

        # 모드/키 정합성 (#28 Sandbox/Paper Keys)
        warnings.extend(self._validate_mode_key_alignment())
        return warnings

    def _validate_mode_key_alignment(self) -> list[str]:
        """TRADING_MODE 와 채워진 키 종류의 일치성 점검 (#28)."""
        out: list[str] = []
        live_keys_present = bool(
            self.upbit_access_key or self.upbit_secret_key
            or self.okx_api_key or self.okx_api_secret or self.okx_api_password
        )
        sandbox_keys_present = bool(
            self.okx_api_key_sandbox or self.okx_api_secret_sandbox
            or self.okx_api_password_sandbox
            or self.binance_api_key_sandbox or self.binance_api_secret_sandbox
        )

        # PAPER/SIMULATION 인데 LIVE 키가 .env 에 채워져 있음 → footgun 알림
        if (self.trading_mode in {TradingMode.PAPER, TradingMode.SIMULATION}
                and live_keys_present):
            out.append(
                f"TRADING_MODE={self.trading_mode.value} 인데 LIVE 거래소 키가 "
                ".env 에 채워져 있음 — sandbox/paper 모드에서는 LIVE 키 제거 권장 "
                "(docs/sandbox_paper_keys.md §3, §4.3)"
            )

        # 같은 거래소에 LIVE/SANDBOX 키가 모두 채워져 있음 → 헷갈림 위험
        if (self.okx_api_key and self.okx_api_key_sandbox):
            out.append(
                "OKX_API_KEY 와 OKX_API_KEY_SANDBOX 가 동시에 채워져 있음 — "
                "한 환경에 한 종류만 두는 것을 권장 (#28)"
            )
        return out


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """테스트 헬퍼 — get_settings 의 lru_cache 초기화."""
    get_settings.cache_clear()


# ── env var 카탈로그 (테스트로 .env.example 파리티 강제) ──────────

# 본 파일의 Settings 가 직접 참조하는 env 변수 이름. .env.example 에 반드시
# 동일 키가 존재해야 한다 (test_config_layer.py 가 회귀 검증).
# ─────────────────────────────────────────────────────────────────
# 체크리스트 #9: 신규 pydantic-settings 계층 재export.
#
# `app.core.settings` 가 nested + SecretStr + YAML 지원 Settings 를 제공한다.
# 본 파일의 legacy `Settings` 와 충돌하지 않도록 신규 타입은 `AutoTradeSettings`
# 라는 이름으로 노출한다. 신규 코드 권장:
#
#   from app.core.settings import get_app_settings
#   settings = get_app_settings()
#
# 본 파일에서 직접:
#
#   from app.core.config import AutoTradeSettings, get_app_settings
# ─────────────────────────────────────────────────────────────────
from .settings import (  # noqa: E402  (legacy 코드와의 분리 유지)
    Settings as AutoTradeSettings,
    get_app_settings,
    reset_app_settings_cache,
)


ENV_VARS_REFERENCED: tuple[str, ...] = (
    "TRADING_MODE",
    "ENABLE_LIVE_TRADING",
    "ENABLE_AI_EXECUTION",
    "ENABLE_KIMP_STRATEGY",
    "ENABLE_AI_AGENTS",
    "ENABLE_OKX",
    "ENABLE_UPBIT",
    "DEMO_MODE",
    "MAX_ORDER_NOTIONAL_USDT",
    "DAILY_LOSS_LIMIT_PCT",
    "MAX_OPEN_POSITIONS",
    "MAX_LEVERAGE",
    "RE_ENTRY_COOLDOWN_MIN",
    "MAX_CONSECUTIVE_LOSSES",
    "FRESHNESS_THRESHOLD_SEC",
    "WATCHLIST_MAX_ENABLED_TOTAL",
    "MARKET_COLLECTOR_MAX_SYMBOLS",
    "UPBIT_ACCESS_KEY",
    "UPBIT_SECRET_KEY",
    "OKX_API_KEY",
    "OKX_API_SECRET",
    "OKX_API_PASSWORD",
    "OKX_API_KEY_SANDBOX",
    "OKX_API_SECRET_SANDBOX",
    "OKX_API_PASSWORD_SANDBOX",
    "BINANCE_API_KEY_SANDBOX",
    "BINANCE_API_SECRET_SANDBOX",
    "ANTHROPIC_API_KEY",
    "AI_MODEL",
    "TELEGRAM_TOKEN",
    "TELEGRAM_CHAT_ID",
    "ADMIN_TOKEN",
    "EXCHANGERATE_API_KEY",
    "USDT_KRW_FALLBACK",
)
