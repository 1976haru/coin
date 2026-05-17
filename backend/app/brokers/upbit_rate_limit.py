"""Upbit Remaining-Req / Rate Limit 헬퍼 — 체크리스트 #21.

업비트는 API 그룹별로 분/초 단위 잔여 요청 수를 ``Remaining-Req`` 응답 헤더로
알려준다. 예: ``group=market; min=599; sec=9``.

본 모듈은 다음을 제공한다.

  - ``parse_remaining_req(header)`` — 헤더 문자열 → dict.
  - ``should_throttle(remaining, min_remaining)`` — throttle 필요 여부.
  - ``RateLimitState`` — 최신 잔여 요청 수를 상태로 보관 (read-only adapter 진단용).

설계 원칙:
  - 본 모듈은 외부 네트워크/sleep 을 절대 호출하지 않는다. throttle 결정만 반환.
  - 실제 sleep 은 caller 가 적용 — 테스트가 느려지지 않도록 sleep 함수를 주입한다.
  - 비어 있거나 형식이 깨진 헤더는 안전하게 ``{}`` / False 처리.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable


_VALID_KEYS = frozenset({"group", "min", "sec"})


def parse_remaining_req(header: str | None) -> dict:
    """``Remaining-Req`` 헤더를 dict 로 파싱.

    예시:
        >>> parse_remaining_req("group=market; min=599; sec=9")
        {'group': 'market', 'min': 599, 'sec': 9}

    안전 처리:
      - None / 빈 문자열 → ``{}``
      - 형식이 깨진 토큰은 건너뛰고, 인식 가능한 키만 채운다.
      - min/sec 의 값이 정수가 아니면 해당 항목만 제외.
    """
    if not header or not isinstance(header, str):
        return {}
    out: dict[str, str | int] = {}
    for raw in header.split(";"):
        token = raw.strip()
        if not token or "=" not in token:
            continue
        k, _, v = token.partition("=")
        key = k.strip().lower()
        val = v.strip()
        if key not in _VALID_KEYS or not val:
            continue
        if key == "group":
            out[key] = val
        else:
            try:
                out[key] = int(val)
            except ValueError:
                # 형식이 깨진 숫자 토큰은 무시
                continue
    return out


def should_throttle(remaining: dict, *, min_remaining: int = 1) -> bool:
    """잔여 요청 수가 안전 임계값 이하인지 판단.

    Parameters
    ----------
    remaining:
        ``parse_remaining_req`` 결과 dict.
    min_remaining:
        ``sec`` 잔여가 이 값 이하면 throttle 필요로 판단 (기본 1).

    빈 dict 는 알 수 없음 → False (caller 가 보수적으로 처리하려면 별도 정책).
    """
    if not remaining:
        return False
    if min_remaining < 0:
        min_remaining = 0
    sec = remaining.get("sec")
    if isinstance(sec, int) and sec <= min_remaining:
        return True
    minute = remaining.get("min")
    # 분 단위 잔여가 0 이면 즉시 throttle (보수적).
    if isinstance(minute, int) and minute <= 0:
        return True
    return False


@dataclass
class RateLimitState:
    """가장 최근 Remaining-Req 잔여 상태를 보관 (진단/모니터링용).

    sleep 은 caller 가 결정 — 본 객체는 결정에 필요한 정보만 노출한다.
    """

    last_group: str = ""
    last_min: int | None = None
    last_sec: int | None = None
    # 최근 throttle 결정 횟수 (테스트/관제용).
    throttle_count: int = 0
    # caller 가 주입할 수 있는 (가짜) sleep 함수. None 이면 sleep 안 함.
    sleep_fn: Callable[[float], None] | None = field(default=None, repr=False)

    def update(self, header: str | None) -> dict:
        """헤더를 파싱해 상태에 반영하고 dict 를 반환."""
        parsed = parse_remaining_req(header)
        if "group" in parsed:
            self.last_group = str(parsed["group"])
        if "min" in parsed and isinstance(parsed["min"], int):
            self.last_min = parsed["min"]
        if "sec" in parsed and isinstance(parsed["sec"], int):
            self.last_sec = parsed["sec"]
        return parsed

    def maybe_throttle(
        self,
        *,
        min_remaining: int = 1,
        sleep_seconds: float = 0.2,
    ) -> bool:
        """현재 상태가 throttle 임계를 넘으면 ``sleep_fn`` 호출. 호출 여부 반환."""
        state = {
            "group": self.last_group,
            "min": self.last_min,
            "sec": self.last_sec,
        }
        if not should_throttle(state, min_remaining=min_remaining):
            return False
        self.throttle_count += 1
        if self.sleep_fn is not None:
            self.sleep_fn(sleep_seconds)
        return True


__all__ = (
    "parse_remaining_req",
    "should_throttle",
    "RateLimitState",
)
