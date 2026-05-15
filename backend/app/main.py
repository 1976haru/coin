"""Agent Trader Crypto OS — FastAPI entry.

라우터는 app/api/ 하위에서 조립된다 (api_router).
정적 파일은 우선순위:
  1. frontend/dist/ (Vite 프로덕션 빌드, 체크리스트 #7) — 있으면 이것 사용
  2. frontend/        (레거시 데모 HTML 폴백)
둘 다 없으면 정적 마운트 생략.
"""
import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.core.app_info import APP_NAME, APP_VERSION, APP_TAGLINE
from app.api import api_router

app = FastAPI(
    title=APP_NAME,
    version=APP_VERSION,
    description=APP_TAGLINE,
)

if os.path.isdir("frontend/dist"):
    app.mount("/static", StaticFiles(directory="frontend/dist"), name="static")
elif os.path.isdir("frontend"):
    app.mount("/static", StaticFiles(directory="frontend"), name="static")

app.include_router(api_router)
