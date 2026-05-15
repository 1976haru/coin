"""체크리스트 #4 산출물 회귀 테스트.

`docs/safety_principles.md` 와 `CLAUDE.md` 의 핵심 안전 조항이 항상 존재하는지 검증.
누군가 안전 섹션을 통째로 지우거나 단일 경로 다이어그램을 비우면 CI가 막는다.
"""
from pathlib import Path

import pytest


REPO_ROOT  = Path(__file__).resolve().parents[2]
SAFETY_DOC = REPO_ROOT / "docs" / "safety_principles.md"
CLAUDE_DOC = REPO_ROOT / "CLAUDE.md"


REQUIRED_SECTIONS_SAFETY = [
    "## 1. 무엇이 위험한가",
    "## 2. 절대 금지",
    "## 3. AI Agent 안전 원칙",
    "## 4. 단일 주문 경로",
    "## 5. 위험 플래그 기본 false",
    "## 6. 자동 진입 차단 조건",
    "## 7. 모듈 경계",
    "## 8. 승격 정책",
    "## 9. 감사 로그",
    "## 10. 강제 메커니즘 요약",
    "## 11. 운영 시 사용자 자체 점검 체크리스트",
]


REQUIRED_PHRASES_SAFETY = [
    "Single Order Path",
    "OrderGateway",
    "PermissionGate",
    "AuditLog",
    "is_order_intent",
    "ENABLE_WITHDRAWAL",
    "ENABLE_LIVE_TRADING",
    "_legacy_innogrit",
    "RiskOfficerAgent",
    "ExecutionRecommender",
    "PASS 는 실거래 허가가 아니다",
    "frontend",
    "GitHub Pages",
    "WATCH_ONLY",
    "route_order",
]


REQUIRED_TEST_REFERENCES = [
    "test_agents_do_not_import_brokers",
    "test_strategies_do_not_import_brokers",
    "test_dangerous_flags_default_false",
    "test_withdrawal_flag_permanently_false",
]


@pytest.fixture(scope="module")
def safety() -> str:
    assert SAFETY_DOC.exists(), f"missing: {SAFETY_DOC}"
    return SAFETY_DOC.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def claude() -> str:
    assert CLAUDE_DOC.exists(), f"missing: {CLAUDE_DOC}"
    return CLAUDE_DOC.read_text(encoding="utf-8")


# ── safety_principles.md 구조 ─────────────────────────────────────

def test_doc_present_and_substantial(safety):
    assert len(safety) > 4000, "safety_principles.md is suspiciously short"


@pytest.mark.parametrize("section", REQUIRED_SECTIONS_SAFETY)
def test_required_sections(safety, section):
    assert section in safety, f"missing section: {section}"


@pytest.mark.parametrize("phrase", REQUIRED_PHRASES_SAFETY)
def test_required_phrases(safety, phrase):
    assert phrase in safety, f"missing phrase: {phrase}"


@pytest.mark.parametrize("ref", REQUIRED_TEST_REFERENCES)
def test_doc_cites_enforcement_tests(safety, ref):
    """각 안전 규칙은 자동 강제 수단(테스트 이름)을 인용해야 한다."""
    assert ref in safety, f"safety doc must cite enforcement test: {ref}"


def test_doc_documents_single_order_path(safety):
    """단일 주문 경로의 핵심 단계가 모두 명시."""
    for stage in [
        "StrategySignal", "AgentReview", "RiskManager",
        "OrderGuard", "PermissionGate", "ApprovalQueue",
        "OrderGateway", "PaperExecutor", "AuditLog",
    ]:
        assert stage in safety, f"single-path stage missing: {stage}"


def test_doc_links_to_claude_md(safety):
    assert "CLAUDE.md" in safety


# ── CLAUDE.md 핵심 보장 ──────────────────────────────────────────

CLAUDE_REQUIRED_PHRASES = [
    "Agent Trader Crypto OS v1",
    "ENABLE_LIVE_TRADING",
    "ENABLE_WITHDRAWAL",
    "OrderGateway",
    "단일 주문 경로",
    "_legacy_innogrit",
    "is_order_intent",
    "RiskOfficerAgent",
]


@pytest.mark.parametrize("phrase", CLAUDE_REQUIRED_PHRASES)
def test_claude_md_contains_phrase(claude, phrase):
    assert phrase in claude, f"CLAUDE.md missing: {phrase}"


def test_claude_md_lists_dangerous_flags_table(claude):
    """위험 플래그 표가 있어야 한다."""
    for flag in [
        "ENABLE_LIVE_TRADING",
        "ENABLE_AI_EXECUTION",
        "ENABLE_CRYPTO_FUTURES_LIVE",
        "ENABLE_LIVE_ORDER_SUBMISSION",
        "ENABLE_WITHDRAWAL",
    ]:
        assert flag in claude, f"CLAUDE.md missing flag: {flag}"


# ── 안전 원칙과 코드의 살아있는 연결 ─────────────────────────────

def test_doc_referenced_files_actually_exist():
    """safety_principles.md 가 인용하는 모듈 파일이 실제 존재."""
    expected_files = [
        REPO_ROOT / "backend" / "app" / "core" / "feature_flags.py",
        REPO_ROOT / "backend" / "app" / "core" / "modes.py",
        REPO_ROOT / "backend" / "app" / "audit" / "audit_log.py",
        REPO_ROOT / "backend" / "app" / "agents" / "orchestrator.py",
        REPO_ROOT / "backend" / "app" / "governance" / "promotion_gates.py",
        REPO_ROOT / ".github" / "workflows" / "security-ci.yml",
    ]
    for f in expected_files:
        assert f.exists(), f"safety doc references non-existent file: {f.relative_to(REPO_ROOT)}"
