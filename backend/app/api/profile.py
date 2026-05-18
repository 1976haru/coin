"""환경 프로파일 상태 라우터 — 체크리스트 #28.

- GET /api/profile — startup guard 결과의 read-only snapshot.

응답은 secret 을 절대 평문으로 노출하지 않는다 — ``mask_secret`` 으로 모든 secret
변수가 마스킹된 채로 노출된다 (`masked_env_summary`).

본 라우터는 critical violation 이 있어도 시작을 차단하지 않는다 (운영자가 확인
후 조치). strict mode 강제 차단은 `enforce_startup_profile(strict=True)` 를 별도
배포 스크립트에서 호출한다.
"""
from __future__ import annotations
from datetime import datetime, timezone

from fastapi import APIRouter

from app.core.profile import validate_startup_profile


router = APIRouter()


@router.get("/api/profile")
def get_profile_status():
    """현재 startup guard snapshot.

    응답에는 모든 secret 환경변수가 ``mask_secret`` 으로 마스킹된 채로 노출된다.
    원본 값은 포함되지 않는다.
    """
    result = validate_startup_profile()
    return {
        **result.to_dict(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "warning": (
            "Profile snapshot is read-only. Secrets are masked. "
            "Run `enforce_startup_profile(strict=True)` in deploy script "
            "to enforce critical violations."
        ),
    }
