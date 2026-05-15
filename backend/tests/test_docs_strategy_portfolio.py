"""체크리스트 #2 산출물 회귀 테스트.

`docs/strategy_portfolio.md` 가 4대 전략 + 장세 매트릭스 + 김프 특수 정책을
항상 담고 있는지 검증.
"""
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
DOC = REPO_ROOT / "docs" / "strategy_portfolio.md"


REQUIRED_STRATEGIES = [
    "Trend Following",
    "Volatility Breakout",
    "Pair Trading",
    "Kimp Mean Reversion",
]


REQUIRED_SECTIONS = [
    "## 1. 4대 전략 카드",
    "## 2. 전략별 상세",
    "## 3. 장세 × 전략 활성 매트릭스",
    "## 4. 역김프 특수 정책",
    "## 5. 새 전략 추가 절차",
    "## 6. 모듈 경계",
]


REQUIRED_PHRASES = [
    "signal-only",
    "is_order_intent=false",
    "StrategySelectionAgent",
    "상시 주력전략 금지",
    "ENABLE_KIMP_STRATEGY",
    "RiskOfficerAgent",
    "OPEN_REVERSE_KIMP",
    "BrokerAdapter",
]


REGIMES = ["TREND_UP", "TREND_DOWN", "RANGE", "BREAKOUT", "HIGH_VOL", "KIMP_GAP", "UNCERTAIN"]


@pytest.fixture(scope="module")
def text() -> str:
    assert DOC.exists(), f"missing doc: {DOC}"
    return DOC.read_text(encoding="utf-8")


def test_doc_present_and_nonempty(text):
    assert len(text) > 2000, "strategy_portfolio.md is suspiciously short"


@pytest.mark.parametrize("strat", REQUIRED_STRATEGIES)
def test_all_four_strategies_present(text, strat):
    assert strat in text, f"missing strategy: {strat}"


@pytest.mark.parametrize("section", REQUIRED_SECTIONS)
def test_required_sections_present(text, section):
    assert section in text, f"missing section: {section}"


@pytest.mark.parametrize("phrase", REQUIRED_PHRASES)
def test_required_phrases_present(text, phrase):
    assert phrase in text, f"missing required phrase: {phrase}"


@pytest.mark.parametrize("regime", REGIMES)
def test_regime_matrix_lists_all_regimes(text, regime):
    assert regime in text, f"missing regime in matrix: {regime}"


def test_kimp_marked_as_event_only():
    """역김프가 'event-only' 또는 '특수'로 분류되어야 한다."""
    body = DOC.read_text(encoding="utf-8")
    assert "event-only" in body or "특수전략" in body or "이벤트 전용" in body


def test_links_to_actual_code_paths():
    """문서가 실제 코드 파일 경로를 인용 (drift 방지)."""
    body = DOC.read_text(encoding="utf-8")
    assert "app/strategies/strategies.py" in body
    assert "app/strategies/kimp_mean_reversion.py" in body
