"""SQLAlchemy engine + session 팩토리 — 체크리스트 #13.

설계:
  - DATABASE_URL 환경변수로 결정. 미설정 시 sqlite:///logs/agent_trader.db
  - engine은 lazy 생성 → 모듈 import 시 부작용 없음 → 기존 테스트 회귀 위험 최소화
  - 테스트는 reset_engine() 으로 초기화 후 in-memory sqlite를 주입
"""
from __future__ import annotations
import os
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker, Session


_DEFAULT_URL = "sqlite:///logs/agent_trader.db"

_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None


def get_database_url() -> str:
    return os.getenv("DATABASE_URL") or _DEFAULT_URL


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        url = get_database_url()
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        _engine = create_engine(url, future=True, connect_args=connect_args)
    return _engine


def get_session_factory() -> sessionmaker:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            bind=get_engine(), expire_on_commit=False, future=True,
        )
    return _SessionLocal


def reset_engine() -> None:
    """테스트 헬퍼 — engine과 session factory를 리셋한다."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


@contextmanager
def session_scope() -> Iterator[Session]:
    """commit/rollback 자동 관리하는 세션 컨텍스트."""
    Sf = get_session_factory()
    s = Sf()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def create_all_tables() -> None:
    """개발/테스트 전용: 모든 테이블 즉시 생성. 운영은 Alembic 사용."""
    from .models import Base
    Base.metadata.create_all(bind=get_engine())
