"""ContractRegistry — 체크리스트 #29 (확장).

`StrategyContract` ABC 를 따르는 신규 전략 클래스의 등록소. 기존 `StrategyRegistry`
(Protocol 기반) 와는 별도 — 본 registry 는 `StrategyContract` 하위 클래스만 허용.

설계 원칙:
  - 같은 name 중복 등록 방지.
  - `StrategyContract` 하위 클래스만 등록 가능 — `_extract_capability` 가 검증.
  - `enabled_by_default=False` 기본 — 운영자가 명시적으로 활성화.
  - regime / symbol / enabled 필터 제공 — `StrategySelectionAgent` 가 사용.
  - registry 자체는 broker / adapter / order_gateway 를 알지 못한다.
"""
from __future__ import annotations
from typing import Any, Iterable

from app.strategies.base import StrategyCapability
from app.strategies.contract import StrategyContract


# ── Entry ────────────────────────────────────────────────────────


class _Entry:
    """등록된 전략의 메타데이터."""

    __slots__ = ("cls", "config_factory", "enabled", "capability",
                 "preferred_regimes")

    def __init__(
        self,
        cls: type[StrategyContract],
        *,
        config_factory: Any | None = None,
        enabled: bool = False,
    ):
        if not isinstance(cls, type) or not issubclass(cls, StrategyContract):
            raise TypeError(
                f"{cls!r} 는 StrategyContract 의 하위 클래스가 아니다 "
                "(ContractRegistry 는 ABC 구현만 허용)"
            )
        self.cls = cls
        self.config_factory = config_factory
        self.enabled = bool(enabled)
        # 클래스 속성 확인 — capability/preferred_regimes 는 cls 에 있어야.
        cap = getattr(cls, "capability", None)
        if not isinstance(cap, StrategyCapability):
            raise TypeError(
                f"{cls.__name__}.capability 가 StrategyCapability 가 아님"
            )
        self.capability = cap
        regimes = getattr(cls, "preferred_regimes", ("UNKNOWN",))
        if not isinstance(regimes, (tuple, list)):
            raise TypeError(
                f"{cls.__name__}.preferred_regimes 는 tuple/list 이어야 함"
            )
        self.preferred_regimes = tuple(regimes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.capability.name,
            "description": self.capability.description,
            "enabled": self.enabled,
            "preferred_regimes": list(self.preferred_regimes),
            "capability": self.capability.to_dict(),
            "is_final_order_size": False,
            "direct_order_allowed": False,
        }


# ── ContractRegistry ─────────────────────────────────────────────


class ContractRegistry:
    """`StrategyContract` 전용 registry — 필터 + activation 메타데이터.

    공개 메서드:
      - register_strategy(cls, *, enabled=False, config_factory=None)
      - get_strategy(name) → cls
      - list_strategies() → list[name]
      - create_strategy(name, config=None) → instance
      - filter_by_market_regime(regime) → list[Entry]
      - filter_by_symbol(symbol) → list[Entry] (capability.supports_pair 등 메타)
      - filter_enabled() → list[Entry]
      - catalog() → list[dict] (UI/API 용)
      - set_enabled(name, enabled)
    """

    def __init__(self):
        self._entries: dict[str, _Entry] = {}

    # ── 등록 / 해제 ─────────────────────────────────────────────

    def register_strategy(
        self,
        cls: type[StrategyContract],
        *,
        enabled: bool = False,
        config_factory: Any | None = None,
    ) -> _Entry:
        """전략 클래스 등록.

        - 같은 name 중복 → ``ValueError``.
        - `StrategyContract` 하위 아니면 ``TypeError`` (Entry 생성 시).
        """
        entry = _Entry(cls, enabled=enabled, config_factory=config_factory)
        name = entry.capability.name
        if not name:
            raise ValueError("strategy capability.name 이 비어 있음")
        if name in self._entries:
            raise ValueError(
                f"strategy name {name!r} 가 이미 등록됨 (중복 등록 금지)"
            )
        self._entries[name] = entry
        return entry

    def unregister(self, name: str) -> bool:
        return self._entries.pop(name, None) is not None

    def clear(self) -> None:
        self._entries.clear()

    # ── 조회 ────────────────────────────────────────────────────

    def get_strategy(self, name: str) -> type[StrategyContract] | None:
        e = self._entries.get(name)
        return e.cls if e is not None else None

    def get_entry(self, name: str) -> _Entry | None:
        return self._entries.get(name)

    def list_strategies(self) -> list[str]:
        return sorted(self._entries.keys())

    def all_entries(self) -> list[_Entry]:
        return [self._entries[k] for k in self.list_strategies()]

    def catalog(self) -> list[dict[str, Any]]:
        return [e.to_dict() for e in self.all_entries()]

    # ── 생성 ────────────────────────────────────────────────────

    def create_strategy(
        self,
        name: str,
        config: dict[str, Any] | None = None,
    ) -> StrategyContract:
        """전략 인스턴스 생성.

        - ``config_factory`` 가 등록되어 있으면 ``config_factory(cls, config)``
          호출 결과 사용.
        - 아니면 ``cls(**config)`` 또는 ``cls()`` 호출.
        """
        e = self._entries.get(name)
        if e is None:
            raise KeyError(f"unknown strategy: {name!r}")
        cfg = dict(config or {})
        if e.config_factory is not None:
            return e.config_factory(e.cls, cfg)
        try:
            return e.cls(**cfg) if cfg else e.cls()
        except TypeError as exc:
            raise TypeError(
                f"strategy {name!r} 생성 실패 — config={cfg}: {exc}"
            ) from exc

    # ── 필터 ────────────────────────────────────────────────────

    def filter_by_market_regime(self, regime: str) -> list[_Entry]:
        """regime 이 ``UNKNOWN`` 이거나 entry 의 preferred_regimes 에 포함되면 후보."""
        r = (regime or "UNKNOWN").upper()
        out: list[_Entry] = []
        for e in self.all_entries():
            prefs = tuple(p.upper() for p in e.preferred_regimes)
            if r in prefs or r == "UNKNOWN" or "ANY" in prefs:
                out.append(e)
        return out

    def filter_by_symbol(self, symbol: str) -> list[_Entry]:
        """capability.supports_pair 등 메타데이터 기반 필터.

        현재 단계는 보수적으로 — pair 전략은 symbol 단일 입력으로는 부적합으로
        간주, capability.supports_pair=True 인 entry 는 제외.
        """
        out: list[_Entry] = []
        for e in self.all_entries():
            if e.capability.supports_pair:
                # pair 전략은 (A, B) 두 심볼이 필요 — 단일 symbol 필터에서는 제외.
                continue
            out.append(e)
        return out

    def filter_enabled(self) -> list[_Entry]:
        return [e for e in self.all_entries() if e.enabled]

    def set_enabled(self, name: str, enabled: bool) -> bool:
        e = self._entries.get(name)
        if e is None:
            return False
        e.enabled = bool(enabled)
        return True


def build_empty_registry() -> ContractRegistry:
    """기본 비어 있는 registry — 호출자가 명시적으로 register."""
    return ContractRegistry()


__all__ = (
    "ContractRegistry",
    "build_empty_registry",
)
