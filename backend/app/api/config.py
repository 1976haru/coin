"""Config 라우터 — 체크리스트 #9.

- GET /api/config/warnings  (공개)  : Settings.validate() 결과 (운영 경고)
- GET /api/config/effective (admin) : Settings.summary() — secret 마스킹된 전체 설정 스냅샷
"""
from fastapi import APIRouter, Depends

from .deps import settings, verify_admin


router = APIRouter()


@router.get("/api/config/warnings")
def config_warnings():
    """Settings.validate() — 운영 경고 리스트. 비공개 정보 없음."""
    return {"warnings": settings.validate()}


@router.get("/api/config/effective")
def config_effective(_=Depends(verify_admin)):
    """Settings.summary() — secret 마스킹된 전체 설정 스냅샷."""
    return settings.summary()
