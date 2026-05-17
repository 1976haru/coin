"""체크리스트 #6: 최소 로깅 설정.

추후 구조화 로깅/외부 sink/감사로그 분리 확장을 위한 최소 골격만 제공한다.
이 단계에서는 ``logging.basicConfig`` 한 번 호출이 전부이며, 호출은 멱등하다.

- LOG_LEVEL 환경변수로 레벨 조정 (기본 INFO)
- secret 은 로그에 직접 찍히지 않게 호출자 책임 (감사로그 redaction 은 별도 체크리스트)
"""
from __future__ import annotations

import logging
import os

_DEFAULT_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
_configured = False


def setup_logging(level: str | None = None) -> None:
    """프로세스 전체 로깅 1회 초기화. 멱등."""
    global _configured
    if _configured:
        return
    lvl_name = (level or os.getenv("LOG_LEVEL", "INFO")).upper()
    lvl = getattr(logging, lvl_name, logging.INFO)
    logging.basicConfig(level=lvl, format=_DEFAULT_FORMAT)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    """모듈용 로거 헬퍼. ``setup_logging`` 을 자동 호출한다."""
    setup_logging()
    return logging.getLogger(name)
