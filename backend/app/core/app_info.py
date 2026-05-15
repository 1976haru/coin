"""앱 메타 정보 + 릴리즈 노트 — 체크리스트 #5 Agent Trader Naming.

frontend / UI / AuditLog / API 에서 일관된 이름·버전·릴리즈 노트를 노출하기 위한
단일 진리 소스. 새 버전 릴리즈 시 RELEASE_NOTES 상단에 항목 추가.

원칙:
- 브랜드 변경만. 주문 로직 변경 금지 (체크리스트 #5 보안 주의).
- APP_VERSION 은 SemVer 권장. 알파/베타 단계는 -alpha/-beta 접미사.
- 릴리즈 노트는 사용자(비개발자) 가 읽는 글이므로 한국어 친화적으로.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict


APP_NAME    = "Agent Trader Crypto OS"
APP_VERSION = "1.0.0-alpha"
APP_TAGLINE = "AI Agent 기반 코인 자동매매 연구·검증·관제 플랫폼"
APP_REPO    = "https://github.com/1976haru/coin"


@dataclass(frozen=True)
class ReleaseNote:
    version: str
    date: str            # ISO YYYY-MM-DD
    title: str
    highlights: tuple[str, ...]


# 최신이 위. 추가 시 새 ReleaseNote 를 리스트 맨 앞에.
RELEASE_NOTES: tuple[ReleaseNote, ...] = (
    ReleaseNote(
        version="1.0.0-alpha",
        date="2026-05-10",
        title="Agent Trader Crypto OS v1 — 초기 알파 (구조 정렬)",
        highlights=(
            "정체성 변경: INNOGRiT v2 → Agent Trader Crypto OS v1",
            "디렉토리 정렬: api/audit/brokers/governance/schemas/db 신설",
            "단일 주문 경로 강제 (RiskManager → PermissionGate → OrderGateway)",
            "ModeCapability 매트릭스 도입 (모드 × 9개 행동)",
            "체크리스트 #1-4 산출물 + 회귀 테스트 169개",
            "기본 모드 PAPER, 모든 LIVE 플래그 false",
            "이전 이노그릿 코드는 backend/_legacy_innogrit/ 로 격리",
        ),
    ),
)


def app_info() -> dict:
    return {
        "name":    APP_NAME,
        "version": APP_VERSION,
        "tagline": APP_TAGLINE,
        "repo":    APP_REPO,
    }


def _note_to_dict(n: ReleaseNote) -> dict:
    d = asdict(n)
    # asdict 는 tuple 을 tuple 로 유지 → JSON/UI 친화적으로 list 변환
    d["highlights"] = list(d["highlights"])
    return d


def release_notes() -> list[dict]:
    """모든 릴리즈 노트 (최신 우선) 직렬화."""
    return [_note_to_dict(n) for n in RELEASE_NOTES]


def latest_release() -> dict | None:
    """가장 최근 릴리즈 노트 한 건."""
    return _note_to_dict(RELEASE_NOTES[0]) if RELEASE_NOTES else None
