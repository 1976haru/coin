"""체크리스트 #9 Config Layer — pydantic-settings 기반 신규 설정 계층.

본 모듈은 신규 nested Settings 를 제공한다. 기존 `app.core.config.Settings`
(frozen dataclass, 1300+ 회귀 테스트 의존) 는 그대로 보존되며, 본 모듈은
별도 namespace 로 공존한다.

설정 우선순위 (높음 → 낮음):
  1. init 인자 (테스트 override)
  2. OS 환경변수 (AUTOTRADE_ prefix, __ nested delimiter)
  3. .env 파일
  4. config/config.yaml
  5. 코드 default

보안 원칙 (CLAUDE.md §2.1):
  - secret 은 `.env` 또는 OS 환경변수에서만 읽는다.
  - `config/config.yaml` 에 broker.api_key / api_secret / account_no /
    access_token 등 secret 류 키가 들어가면 ValueError 로 즉시 차단.
  - secret 필드는 `SecretStr` — repr/print/JSON 직렬화 시 마스킹.

live 모드는 enum/설정값으로만 존재. 실제 실거래 전환은 본 단계에서 구현하지
않는다. `allow_live_trading=False` (기본) 이면 live 모드 + 실행을 모두 차단한다.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional, Tuple, Type

import yaml
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from app.schemas.enums import TradingMode

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────
# YAML 소스 (secret guard 포함)
# ─────────────────────────────────────────────────────────────────

# config.yaml 에 절대 들어가서는 안 되는 키 (서브 키 이름 기준).
# 매칭 시 ValueError → 운영자가 즉시 알 수 있도록 명시적으로 fail-fast.
_FORBIDDEN_SECRET_KEYS = frozenset({
    "api_key",
    "api_secret",
    "secret",
    "secret_key",
    "access_key",
    "access_token",
    "account_no",
    "account_number",
    "passphrase",
    "password",
    "private_key",
    "token",
})


def _scan_secrets(node: Any, path: str = "") -> None:
    """재귀적으로 dict 를 훑으며 금지된 secret 키를 찾는다."""
    if isinstance(node, dict):
        for k, v in node.items():
            key = str(k).lower()
            here = f"{path}.{k}" if path else str(k)
            if key in _FORBIDDEN_SECRET_KEYS and v not in (None, "", {}, []):
                raise ValueError(
                    f"config/config.yaml 에 secret 류 키가 들어 있습니다: '{here}'. "
                    "secret 은 .env 또는 OS 환경변수에서만 읽어야 합니다 "
                    "(CLAUDE.md §2.1)."
                )
            _scan_secrets(v, here)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            _scan_secrets(item, f"{path}[{i}]")


def _default_yaml_path() -> Path:
    """`config/config.yaml` 의 기본 경로 (저장소 루트 기준)."""
    # backend/app/core/settings.py → 저장소 루트는 3 단계 위.
    return Path(__file__).resolve().parents[3] / "config" / "config.yaml"


def load_yaml_config(path: Optional[Path] = None) -> dict:
    """`config.yaml` 을 로드. 없으면 빈 dict 반환 (앱은 default 로 부팅).

    Parameters
    ----------
    path:
        명시 시 그 경로를 읽는다. 미지정 시 저장소 루트 `config/config.yaml`.

    Raises
    ------
    ValueError
        YAML 안에 금지된 secret 류 키가 들어 있을 때.
    RuntimeError
        YAML 파싱 실패 (구문 오류 등).
    """
    p = path or _default_yaml_path()
    if not p.exists():
        log.debug("config.yaml not found at %s — using defaults", p)
        return {}
    try:
        with p.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        raise RuntimeError(f"config.yaml 파싱 실패: {p} — {e}") from e
    if not isinstance(data, dict):
        raise RuntimeError(
            f"config.yaml 최상위가 매핑이어야 합니다 (got {type(data).__name__})"
        )
    _scan_secrets(data)
    return data


class YamlConfigSettingsSource(PydanticBaseSettingsSource):
    """pydantic-settings 용 YAML 소스 어댑터."""

    def __init__(self, settings_cls: Type[BaseSettings], yaml_path: Optional[Path] = None):
        super().__init__(settings_cls)
        self._yaml_path = yaml_path
        self._data = load_yaml_config(yaml_path)

    def get_field_value(self, field, field_name: str):  # type: ignore[override]
        value = self._data.get(field_name)
        return value, field_name, value is not None

    def __call__(self) -> dict:
        return self._data


# ─────────────────────────────────────────────────────────────────
# 그룹별 nested 설정
# ─────────────────────────────────────────────────────────────────

class AppSection(BaseSettings):
    """app 메타."""

    model_config = SettingsConfigDict(extra="ignore")

    name:    str = Field(default="AutoTrade Backend", description="앱 이름")
    env:     str = Field(default="local", description="실행 환경 (local/dev/prod)")
    version: str = Field(default="0.1.0",
                         description="앱 버전 — pyproject 와 별도 관리")


class TradingSection(BaseSettings):
    """거래 모드 + live 안전 가드."""

    model_config = SettingsConfigDict(extra="ignore")

    mode: TradingMode = Field(
        default=TradingMode.PAPER,
        description="paper/mock/live. live 는 본 단계 비활성 — Enum 값만 존재.",
    )
    allow_live_trading: bool = Field(
        default=False,
        description="live 실거래 허용 여부. 기본 False — 추후 governance 승인 시 활성.",
    )
    require_approval_for_live: bool = Field(
        default=True,
        description="live 모드에서도 모든 주문은 사람 승인이 필요한지. 기본 True.",
    )

    @field_validator("mode", mode="before")
    @classmethod
    def _normalize_mode(cls, v: Any) -> Any:
        """env 에서 'PAPER' 같은 대문자가 와도 lowercase 로 정규화."""
        if isinstance(v, str):
            return v.lower()
        return v


class RiskSection(BaseSettings):
    """리스크 한도. 본 단계는 보수적 default."""

    model_config = SettingsConfigDict(extra="ignore")

    max_daily_loss:         str = Field(default="100000",
                                        description="일일 최대 손실 (원 또는 USDT 단위 — str 로 보관)")
    max_position_value:     str = Field(default="1000000",
                                        description="단일 포지션 최대 평가액")
    max_order_value:        str = Field(default="500000",
                                        description="단일 주문 최대 명목가")
    max_open_positions:     int = Field(default=3, ge=0,
                                        description="동시 보유 가능 포지션 수")
    emergency_stop_enabled: bool = Field(
        default=True,
        description="Emergency Stop 활성 여부. 기본 True (안전 우선).",
    )


class BrokerSection(BaseSettings):
    """브로커 연결 — secret 은 OS 환경변수/.env 에서만.

    `api_key`/`api_secret`/`account_no` 는 SecretStr — repr 노출 차단.
    """

    model_config = SettingsConfigDict(extra="ignore")

    provider: str = Field(default="mock",
                          description="mock/paper 외 본 단계 실거래 어댑터 없음")
    base_url: Optional[str] = Field(default=None,
                                    description="브로커 API base URL (없으면 default)")
    api_key:     Optional[SecretStr] = Field(
        default=None,
        description="브로커 API Key. config.yaml 에 절대 두지 말 것 — .env 전용.",
    )
    api_secret:  Optional[SecretStr] = Field(
        default=None,
        description="브로커 API Secret. .env 전용.",
    )
    account_no:  Optional[SecretStr] = Field(
        default=None,
        description="브로커 계좌번호. .env 전용.",
    )


class DatabaseSection(BaseSettings):
    """DB 연결 — url 은 secret 으로 취급."""

    model_config = SettingsConfigDict(extra="ignore")

    url:  Optional[SecretStr] = Field(
        default=None,
        description="DB URL. 인증정보 포함 가능성 → SecretStr 처리.",
    )
    echo: bool = Field(default=False, description="SQLAlchemy echo")


class LoggingSection(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    level: str = Field(default="INFO", description="로깅 레벨")
    # `json` 은 BaseSettings 부모의 메서드명과 충돌 → 필드명은 `json_format`,
    # 외부 키(YAML/env)는 alias `json` 으로 노출하여 스펙 호환 유지.
    json_format: bool = Field(
        default=False, alias="json",
        description="JSON 라인 포맷 출력 여부",
    )


class FlagsSection(BaseSettings):
    """체크리스트 #10: Feature Flags 의 raw 값.

    원칙:
      - 모든 위험 플래그 default False.
      - True 로 set 되어도 그 자체로 실거래/AI 자동실행을 허용하지 않는다.
        실제 허용 여부는 `app.core.feature_flags` 의 `is_*_enabled()` 다중 조건이
        결정한다 (config 와 feature flag 의 역할 분리, 다중 잠금 장치).
      - `enable_kimp_strategy` 도 default False (전략 활성화 자체에도 보수적
        기본값). 실거래 허용과는 무관 — `is_kimp_strategy_enabled()` 참고.
    """

    model_config = SettingsConfigDict(extra="ignore")

    enable_live_trading:        bool = Field(default=False,
        description="실거래 주문 송신 허용 후보. 단독으로는 실거래 활성 X.")
    enable_ai_execution:        bool = Field(default=False,
        description="AI 실행 판단 모듈 활성 후보. 직접 주문 권한 아님.")
    enable_crypto_futures_live: bool = Field(default=False,
        description="코인/선물 실거래 후보. local 환경에서는 강제 False.")
    enable_kimp_strategy:       bool = Field(default=False,
        description="김프 전략 모듈 활성. 실거래 허용 여부와 무관.")


# ─────────────────────────────────────────────────────────────────
# 통합 Settings
# ─────────────────────────────────────────────────────────────────

class Settings(BaseSettings):
    """체크리스트 #9: AutoTrade 신규 Settings (pydantic-settings).

    환경변수 prefix:
      `AUTOTRADE_<SECTION>__<FIELD>` 예: `AUTOTRADE_TRADING__MODE=paper`.

    설정 우선순위 (높음 → 낮음):
      init → OS env → .env → config.yaml → 코드 default
    """

    model_config = SettingsConfigDict(
        env_prefix="AUTOTRADE_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app:      AppSection      = Field(default_factory=AppSection)
    trading:  TradingSection  = Field(default_factory=TradingSection)
    risk:     RiskSection     = Field(default_factory=RiskSection)
    broker:   BrokerSection   = Field(default_factory=BrokerSection)
    database: DatabaseSection = Field(default_factory=DatabaseSection)
    logging:  LoggingSection  = Field(default_factory=LoggingSection)
    flags:    FlagsSection    = Field(default_factory=FlagsSection)

    # ── 우선순위 정의 ────────────────────────────────────────
    @classmethod
    def settings_customise_sources(  # type: ignore[override]
        cls,
        settings_cls: Type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> Tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            YamlConfigSettingsSource(settings_cls),
            file_secret_settings,
        )

    # ── live 모드 안전 가드 ─────────────────────────────────────
    @model_validator(mode="after")
    def _enforce_live_mode_guard(self) -> "Settings":
        """live 모드 + allow_live_trading=False 조합은 거부."""
        if (self.trading.mode == TradingMode.LIVE
                and not self.trading.allow_live_trading):
            raise ValueError(
                "trading.mode='live' 인데 trading.allow_live_trading=False — "
                "live 실거래는 별도 승인 + 환경변수 변경 필요 (CLAUDE.md §2.2/§2.6)."
            )
        return self

    # ── 안전 스냅샷 (secret 마스킹) ─────────────────────────────
    def safe_dump(self) -> dict:
        """health response / 운영 UI 노출용 — SecretStr 은 `**********` 마스킹.

        pydantic v2 의 `model_dump(mode='json')` 는 `SecretStr` 을 `**********`
        문자열로 직렬화한다. alias 가 있는 필드는 alias 이름으로 노출.
        """
        return self.model_dump(mode="json", by_alias=True)


# ─────────────────────────────────────────────────────────────────
# 캐싱 진입점
# ─────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def get_app_settings() -> Settings:
    """앱 전역에서 재사용되는 Settings 싱글톤.

    legacy `app.core.config.get_settings` 와 이름이 다르므로 양쪽 모두 안전하게
    공존한다. 테스트는 `get_app_settings.cache_clear()` 로 초기화.
    """
    return Settings()


def reset_app_settings_cache() -> None:
    """테스트 헬퍼."""
    get_app_settings.cache_clear()
