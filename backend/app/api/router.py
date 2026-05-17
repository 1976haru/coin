"""체크리스트 #6: APIRouter 단일 진입점.

이 파일은 ``app.api.__init__`` 에서 조립된 ``api_router`` 를 그대로 재노출한다.
스펙(체크리스트 #6)이 ``backend/app/api/router.py`` 경로를 요구하므로 추가했으며,
실제 라우터 등록 로직은 기존 ``app/api/__init__.py`` 에 그대로 둔다 (중복 방지).
"""
from . import api_router

__all__ = ["api_router"]
