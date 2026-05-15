"""앱 메타/릴리즈 노트 라우터 — 체크리스트 #5.

/api/app          — 메타 (이름/버전/태그라인/저장소)
/api/release-notes — 모든 릴리즈 노트
/api/release-notes/latest — 최신 한 건

(/api/status 는 health.py 가 담당 — 메타가 이미 포함됨)
"""
from fastapi import APIRouter, HTTPException

from app.core.app_info import app_info, release_notes, latest_release

router = APIRouter()


@router.get("/api/app")
def get_app_info():
    return app_info()


@router.get("/api/release-notes")
def get_release_notes():
    return {"items": release_notes(), "count": len(release_notes())}


@router.get("/api/release-notes/latest")
def get_latest_release():
    latest = latest_release()
    if latest is None:
        raise HTTPException(404, "no release notes available")
    return latest
