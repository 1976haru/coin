"""체크리스트 #5 Agent Trader Naming 회귀.

- 브랜드 드리프트 차단 (frontend HTML, backend API, docs 가 동일 이름 사용)
- /api/app, /api/release-notes 응답 형식 보장
- 릴리즈 노트 데이터 무결성
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.app_info import (
    APP_NAME, APP_VERSION, APP_TAGLINE,
    app_info, release_notes, latest_release, RELEASE_NOTES,
)


client = TestClient(app)
REPO_ROOT = Path(__file__).resolve().parents[2]


# ── 브랜드 드리프트 차단 ─────────────────────────────────────────

EXPECTED_NAME = "Agent Trader Crypto OS"
LEGACY_NAMES_TO_AVOID = ["INNOGRiT Crypto AI", "INNOGRiT v2", "kim_bot"]


def test_app_name_constant():
    assert APP_NAME == EXPECTED_NAME


def test_app_version_uses_semver_like():
    # 1.0.0 또는 1.0.0-alpha 같은 형태
    parts = APP_VERSION.split("-", 1)[0].split(".")
    assert len(parts) == 3, f"version should be SemVer-like, got: {APP_VERSION}"
    for p in parts:
        assert p.isdigit(), f"non-numeric SemVer part: {p}"


def test_tagline_nonempty():
    assert APP_TAGLINE
    assert len(APP_TAGLINE) > 5


# frontend HTML 브랜드 정합성
# 체크리스트 #7 이후: 새 index.html 은 Vite 진입점(미니멀), 실제 UI 는 React 컴포넌트.
# 레거시 단일 HTML 데모는 legacy_demo.html 로 보존되어 mode-tag/release-notes 모달을 유지.

INDEX_HTML        = REPO_ROOT / "frontend" / "index.html"
LEGACY_DEMO_HTML  = REPO_ROOT / "frontend" / "legacy_demo.html"
HEADER_COMPONENT  = REPO_ROOT / "frontend" / "src" / "components" / "Header.tsx"


def test_index_html_present():
    assert INDEX_HTML.exists()


def test_index_html_title_uses_new_brand():
    """Vite 진입점의 <title> 태그가 새 브랜드를 사용."""
    text = INDEX_HTML.read_text(encoding="utf-8")
    assert EXPECTED_NAME in text


@pytest.mark.parametrize("legacy", LEGACY_NAMES_TO_AVOID)
def test_index_html_does_not_carry_legacy_brand(legacy):
    text = INDEX_HTML.read_text(encoding="utf-8")
    assert legacy not in text, f"frontend still references legacy brand: {legacy}"


def test_react_header_component_renders_brand_and_mode():
    """React Header.tsx 가 브랜드/모드/버전을 노출 (id=mode-tag 의 후신)."""
    text = HEADER_COMPONENT.read_text(encoding="utf-8")
    assert "info.name" in text  # appInfo 에서 가져온 브랜드 출력
    assert "version" in text
    assert "trading_mode" in text  # 모드 배지 렌더링
    assert "mode-${status.trading_mode}" in text  # 모드별 클래스


def test_legacy_demo_html_preserves_release_notes_modal():
    """legacy_demo.html 은 기존 데모 기능(릴리즈 노트 모달, 모드 배지)을 보존."""
    text = LEGACY_DEMO_HTML.read_text(encoding="utf-8")
    assert "버전 정보" in text or "릴리즈 노트" in text
    assert "showReleaseNotes" in text
    assert 'id="mode-tag"' in text
    assert 'id="ver-tag"' in text


@pytest.mark.parametrize("legacy", LEGACY_NAMES_TO_AVOID)
def test_legacy_demo_html_does_not_carry_legacy_brand(legacy):
    """레거시 HTML 도 새 브랜드만 사용해야 한다."""
    text = LEGACY_DEMO_HTML.read_text(encoding="utf-8")
    assert legacy not in text


# README 브랜드

def test_readme_uses_new_brand():
    text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert EXPECTED_NAME in text
    for legacy in LEGACY_NAMES_TO_AVOID:
        assert legacy not in text, f"README still references legacy brand: {legacy}"


# frontend appInfo.ts placeholder

def test_appinfo_ts_placeholder_exists():
    p = REPO_ROOT / "frontend" / "src" / "appInfo.ts"
    assert p.exists()
    text = p.read_text(encoding="utf-8")
    assert EXPECTED_NAME in text
    assert "fetchAppInfo" in text
    assert "fetchReleaseNotes" in text


# ── /api/app 엔드포인트 ──────────────────────────────────────────

def test_get_api_app_returns_metadata():
    r = client.get("/api/app")
    assert r.status_code == 200
    d = r.json()
    assert d["name"] == EXPECTED_NAME
    assert d["version"] == APP_VERSION
    assert "tagline" in d
    assert "repo" in d


def test_app_info_function_matches_constants():
    info = app_info()
    assert info["name"] == APP_NAME
    assert info["version"] == APP_VERSION


# ── /api/release-notes 엔드포인트 ───────────────────────────────

def test_get_release_notes_endpoint():
    r = client.get("/api/release-notes")
    assert r.status_code == 200
    d = r.json()
    assert "items" in d
    assert "count" in d
    assert d["count"] == len(d["items"])
    assert d["count"] >= 1


def test_get_latest_release_endpoint():
    r = client.get("/api/release-notes/latest")
    assert r.status_code == 200
    d = r.json()
    for k in ["version", "date", "title", "highlights"]:
        assert k in d


def test_release_note_structure():
    """모든 릴리즈 노트가 필수 필드 보유."""
    for note in RELEASE_NOTES:
        assert note.version
        assert note.date
        assert note.title
        assert isinstance(note.highlights, tuple)
        assert len(note.highlights) >= 1
        # 날짜 형식 YYYY-MM-DD 검증 (단순)
        parts = note.date.split("-")
        assert len(parts) == 3
        assert all(p.isdigit() for p in parts)


def test_release_notes_sorted_newest_first():
    """RELEASE_NOTES 는 날짜 내림차순이어야 한다 (#5 사용성 원칙)."""
    if len(RELEASE_NOTES) < 2:
        pytest.skip("only one release note")
    dates = [n.date for n in RELEASE_NOTES]
    assert dates == sorted(dates, reverse=True), \
        "RELEASE_NOTES must be sorted newest first"


def test_latest_matches_first_in_list():
    assert latest_release() is not None
    assert latest_release()["version"] == RELEASE_NOTES[0].version


def test_release_notes_serialize_as_dicts():
    notes = release_notes()
    assert isinstance(notes, list)
    for n in notes:
        assert isinstance(n, dict)
        assert isinstance(n["highlights"], list)


# ── /api/status 가 app 메타 노출 ─────────────────────────────────

def test_status_carries_app_metadata():
    r = client.get("/api/status")
    d = r.json()
    assert "app" in d
    assert d["app"]["name"] == EXPECTED_NAME


def test_release_note_mentions_current_version():
    """현재 APP_VERSION 이 RELEASE_NOTES 어딘가에 있어야 한다 (drift 방지)."""
    versions = {n.version for n in RELEASE_NOTES}
    assert APP_VERSION in versions, \
        f"current APP_VERSION={APP_VERSION} missing from RELEASE_NOTES"
