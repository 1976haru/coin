"""감사 로그 redaction — 체크리스트 #11 Audit Foundation.

API key, secret, passphrase, token, PII 등 민감 정보를 감사 페이로드에서 자동
마스킹한다. AuditLog.record()가 호출 시점에 적용해 CSV·메모리 모두 안전한
사본만 보관하도록 한다 (CLAUDE.md §2.1.3: secret 로그 잔존 금지).

설계 원칙:
  - 키 이름이 의심 패턴(api_key/secret/passphrase/token/...)을 포함하면 값 마스킹
  - 문자열 값에서 Bearer/Basic 토큰 패턴 마스킹
  - 원본 객체는 변경하지 않고 사본을 반환 (불변 보장)
  - dict/list/tuple은 재귀 처리, 그 외 타입은 그대로 통과
"""
from __future__ import annotations
import re
from typing import Any


REDACTED = "***REDACTED***"

# 키 이름이 이 토큰 중 하나라도 포함하면 값을 마스킹한다.
# (소문자 비교, '-' 는 '_' 로 정규화)
SECRET_KEY_TOKENS: tuple[str, ...] = (
    "api_key",
    "apikey",
    "secret",
    "passphrase",
    "password",
    "token",
    "access_key",
    "private_key",
    "credential",
    "auth",
    "chat_id",       # 텔레그램 채팅 ID — PII로 취급
    "account_number",
    "account_no",
    "ssn",
    "rrn",           # 한국 주민등록번호
)

# Bearer/Basic 인증 헤더 값 마스킹
_BEARER_PATTERN = re.compile(r"(?i)\b(bearer|basic)\s+\S+")


def _matches_secret_key(key: str) -> bool:
    if not isinstance(key, str):
        return False
    k = key.lower().replace("-", "_")
    return any(token in k for token in SECRET_KEY_TOKENS)


def _redact_string(s: str) -> str:
    return _BEARER_PATTERN.sub(lambda m: f"{m.group(1)} {REDACTED}", s)


def _redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return redact(value)
    if isinstance(value, list):
        return [_redact_value(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(v) for v in value)
    if isinstance(value, str):
        return _redact_string(value)
    return value


def redact(payload: Any) -> Any:
    """payload의 민감 필드를 마스킹한 깊은 사본을 반환한다.

    - dict: 키 이름이 secret 패턴을 포함하면 값 전체를 REDACTED로 치환
    - list/tuple: 각 요소 재귀 처리
    - str: Bearer/Basic 토큰 패턴 마스킹
    - 원본 mutate 없음
    """
    if isinstance(payload, dict):
        return {
            k: (REDACTED if _matches_secret_key(k) else _redact_value(v))
            for k, v in payload.items()
        }
    return _redact_value(payload)
