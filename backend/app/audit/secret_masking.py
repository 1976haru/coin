"""Secret 값 마스킹 헬퍼 — 체크리스트 #27.

repr / log 출력 시 secret 값을 평문으로 노출하지 않도록 변환한다.
``app.audit.redaction`` 의 dict/list 자동 마스킹과 달리, 본 모듈은 *단일 값*
에 대한 결정론적 변환을 제공한다.

사용 예:
    >>> mask_secret(None)
    '<unset>'
    >>> mask_secret("")
    '<unset>'
    >>> mask_secret("__SET_IN_LOCAL_ENV_ONLY__")
    '<placeholder>'
    >>> mask_secret("super-secret-real-key-abc123XYZ")
    'su***Yz'
    >>> mask_secret("ab")
    '***'

원칙:
  - 절대 값 전체를 반환하지 않는다 (placeholder 도 그대로 노출하지 않음).
  - prefix/suffix 일부만 보여 운영자가 다른 키와 구분할 수 있게 한다.
  - 길이가 매우 짧으면 prefix/suffix 도 노출하지 않는다 (`***`).
  - None/빈 문자열은 `<unset>` 으로 명확히 표시.
  - 알려진 placeholder 패턴은 `<placeholder>` 로 표시 — repository 에 들어간
    placeholder 값을 평문으로 다시 노출하지 않도록.
"""
from __future__ import annotations
from typing import Any


# placeholder 로 인식할 패턴 — `.env.example` 등 에서 사용.
_PLACEHOLDER_TOKENS: tuple[str, ...] = (
    "__SET_IN_LOCAL_ENV_ONLY__",
    "<set in local env>",
    "<UNSET>",
    "<unset>",
    "PLACEHOLDER",
    "CHANGE_ME",
    "change-me",
    "change-me-local-only",
)


def mask_secret(
    value: Any,
    *,
    prefix_chars: int = 2,
    suffix_chars: int = 2,
    placeholder_label: str = "<placeholder>",
    unset_label: str = "<unset>",
    min_length_for_partial: int = 6,
) -> str:
    """secret 값을 노출하지 않는 마스킹 문자열로 변환.

    Parameters
    ----------
    value:
        마스킹할 값. None / 빈 문자열 / 비문자열 안전 처리.
    prefix_chars / suffix_chars:
        부분 노출할 prefix/suffix 길이.
    placeholder_label:
        값이 알려진 placeholder 일 때 반환할 문자열.
    unset_label:
        값이 None 또는 빈 문자열일 때 반환할 문자열.
    min_length_for_partial:
        이 길이 이상에서만 prefix/suffix 부분 노출. 미만이면 ``***``.

    Returns
    -------
    str — 절대 원본 secret 을 그대로 포함하지 않는다.
    """
    if value is None:
        return unset_label
    # 비문자열은 type 노출만 (실제 값 부재).
    if not isinstance(value, str):
        return f"<non-str:{type(value).__name__}>"
    s = value.strip()
    if not s:
        return unset_label
    if s in _PLACEHOLDER_TOKENS:
        return placeholder_label
    # placeholder 의 일부 변형도 인식 (case-insensitive, 부분일치).
    s_lower = s.lower()
    if any(
        tok.lower() in s_lower
        for tok in ("placeholder", "change_me", "change-me", "set_in_local_env_only")
    ):
        return placeholder_label

    n = len(s)
    pre = max(0, int(prefix_chars))
    suf = max(0, int(suffix_chars))
    # 너무 짧으면 partial 도 노출 안 함.
    if n < max(min_length_for_partial, pre + suf + 1):
        return "***"
    return f"{s[:pre]}***{s[-suf:]}" if suf > 0 else f"{s[:pre]}***"


def mask_dict_values(
    d: dict[str, Any],
    *,
    keys: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """dict 의 secret-like 키 값을 ``mask_secret`` 으로 치환한 사본 반환.

    ``keys`` 미지정 시 redaction.SECRET_KEY_TOKENS 패턴 매칭 (대소문자 무시,
    부분 일치). app.audit.redaction.redact 와 보완 관계 — redaction.redact 는
    ``***REDACTED***`` 로 통째 치환, 본 함수는 prefix/suffix 부분 노출을 허용.
    """
    if not isinstance(d, dict):
        return d  # type: ignore[return-value]
    from .redaction import SECRET_KEY_TOKENS
    targets = tuple(k.lower() for k in (keys or SECRET_KEY_TOKENS))
    out: dict[str, Any] = {}
    for k, v in d.items():
        kl = str(k).lower()
        if any(t in kl for t in targets):
            out[k] = mask_secret(v)
        else:
            out[k] = v
    return out


__all__ = (
    "mask_secret",
    "mask_dict_values",
)
