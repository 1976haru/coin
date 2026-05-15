"""체크리스트 #1 산출물 회귀 테스트.

`docs/product_scope.md` 의 핵심 섹션이 항상 존재하는지 보장한다.
누군가 본문을 통째로 비우거나 안전 원칙 섹션을 지우면 CI가 막는다.
"""
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
DOC = REPO_ROOT / "docs" / "product_scope.md"


REQUIRED_SECTIONS = [
    "## 1. 정체성",
    "## 3. 제품 구조",
    "## 4. MVP 범위 — 포함",
    "## 5. MVP 제외",
    "## 6. 승격 원칙",
    "## 7. AI Agent 원칙",
    "## 8. 비목표",
    "## 9. 측정 가능한 성공 기준",
]


REQUIRED_PHRASES = [
    "Agent Trader Crypto OS v1",
    "signal-only",
    "OrderGateway",
    "PASS는 실거래 허가가 아니다",
    "is_order_intent=false",
    "ENABLE_WITHDRAWAL",
    "허브",                # MOCA 허브+모듈 패턴
    "LIVE_AI_EXECUTION",
]


@pytest.fixture(scope="module")
def text() -> str:
    assert DOC.exists(), f"missing doc: {DOC}"
    return DOC.read_text(encoding="utf-8")


def test_doc_present_and_nonempty(text):
    assert len(text) > 1500, "product_scope.md is suspiciously short"


@pytest.mark.parametrize("section", REQUIRED_SECTIONS)
def test_required_sections_present(text, section):
    assert section in text, f"missing required section: {section}"


@pytest.mark.parametrize("phrase", REQUIRED_PHRASES)
def test_required_phrases_present(text, phrase):
    assert phrase in text, f"missing required phrase: {phrase}"


def test_doc_links_to_safety_principles(text):
    # 안전 원칙으로의 cross-link 가 있어야 함
    assert "safety_principles.md" in text


def test_doc_links_to_claude_md(text):
    assert "CLAUDE.md" in text
