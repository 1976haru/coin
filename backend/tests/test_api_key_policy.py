"""체크리스트 #27 Secret Permissions — 회귀 테스트.

검증:
  1. docs/api_key_policy.md 가 존재하고 핵심 섹션을 포함
  2. ENABLE_WITHDRAWAL 가 코드에서 영구 false (환경변수로 변경 불가)
  3. 어댑터 클래스에 출금 메서드 부재 — 현재까지 정의된 모든 어댑터
  4. .env.example 에 실제 값 없음 (변수명만)
  5. README/CLAUDE.md/safety_principles.md 와 본 정책의 한 줄 요약 일치
  6. 정책 문서가 어댑터/모듈 경계 코드를 정확히 인용
"""
from __future__ import annotations
import os
import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
POLICY = REPO_ROOT / "docs" / "api_key_policy.md"


# ── 1. 문서 존재 + 핵심 섹션 ─────────────────────────────────────

def test_policy_doc_exists():
    assert POLICY.is_file(), "docs/api_key_policy.md 가 존재해야 함 (체크리스트 #27)"


@pytest.mark.parametrize("section", [
    "권한 등급",
    "거래소별 권한 매핑",
    "키 발급 절차",
    "저장",
    "운영 점검표",
    "사고 대응",
    "Incident Response",
])
def test_policy_doc_has_required_sections(section: str):
    text = POLICY.read_text(encoding="utf-8")
    assert section in text, f"api_key_policy.md 에 '{section}' 섹션 누락"


@pytest.mark.parametrize("phrase", [
    "출금 권한 키 영구 금지",
    "ENABLE_WITHDRAWAL",
    "READ_ONLY",
    "IP whitelist",
    "redact",
])
def test_policy_doc_contains_safety_phrases(phrase: str):
    text = POLICY.read_text(encoding="utf-8")
    assert phrase in text, f"api_key_policy.md 에 '{phrase}' 표현 누락"


# ── 2. ENABLE_WITHDRAWAL 영구 false 회귀 ─────────────────────────

def test_enable_withdrawal_is_permanently_false_in_feature_flags():
    """feature_flags.py 에서 enable_withdrawal 가 환경변수와 무관하게 false."""
    from app.core.feature_flags import FeatureFlags
    f = FeatureFlags()
    assert f.enable_withdrawal is False


def test_enable_withdrawal_env_var_is_ignored():
    """ENABLE_WITHDRAWAL=true 환경변수가 있어도 코드는 false 를 강제."""
    os.environ["ENABLE_WITHDRAWAL"] = "true"
    try:
        # FeatureFlags 는 frozen dataclass with field default — 클래스 정의 시점에
        # 평가됨. 그래도 새 인스턴스에서 false 가 나와야 한다 (default 가 False).
        from app.core.feature_flags import FeatureFlags
        f = FeatureFlags()
        assert f.enable_withdrawal is False, \
            "ENABLE_WITHDRAWAL 환경변수가 코드 default 를 덮어쓰면 안 됨"
    finally:
        os.environ.pop("ENABLE_WITHDRAWAL", None)


def test_feature_flags_source_marks_withdrawal_as_permanent():
    """소스 코드에 '영구 false' 주석/표기가 명시되어 있는지."""
    src = (REPO_ROOT / "backend" / "app" / "core" / "feature_flags.py"
           ).read_text(encoding="utf-8")
    # enable_withdrawal 라인이 _bool 호출 없이 직접 False 인지
    line = next((l for l in src.splitlines()
                 if "enable_withdrawal" in l and "=" in l), "")
    assert "_bool(" not in line, \
        "enable_withdrawal 가 환경변수에서 읽히면 안 됨"
    assert "False" in line


# ── 3. 어댑터 출금 메서드 부재 ───────────────────────────────────

def test_no_withdrawal_methods_on_all_adapters():
    """현재까지 정의된 모든 어댑터 클래스가 출금 메서드 보유 안 함."""
    from app.brokers import (
        ExchangeAdapter, MockExchangeAdapter,
        UpbitAdapter, OkxAdapter, BinanceAdapter,
        PaperBroker,
        assert_no_withdrawal_methods,
    )
    for cls in (ExchangeAdapter, MockExchangeAdapter,
                UpbitAdapter, OkxAdapter, BinanceAdapter, PaperBroker):
        assert_no_withdrawal_methods(cls)


def test_no_withdrawal_method_definitions_in_brokers_package():
    """brokers/ 디렉토리의 .py 파일에 출금 관련 ``def`` / ``class`` 정의 부재.

    문서/주석은 정책 자체를 설명하므로 'withdraw' 단어가 등장 가능.
    실제 메서드/클래스 정의(``def *withdraw*`` / ``class *Withdraw*``)만 차단.
    예외: ``assert_no_*`` / ``test_no_*`` 처럼 부재를 검증하는 helper 는 허용.
    """
    forbidden_pattern = re.compile(
        r"^\s*(?:async\s+)?(?:def|class)\s+(\w+)",
    )
    forbidden_substrings = (
        "withdraw", "withdrawal",
        "create_withdrawal", "request_withdrawal",
        "transfer_to_address",
    )
    allowed_prefixes = ("assert_no_", "test_no_", "_assert_no_")

    brokers_dir = REPO_ROOT / "backend" / "app" / "brokers"
    for py in brokers_dir.glob("*.py"):
        text = py.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), start=1):
            m = forbidden_pattern.match(line)
            if not m:
                continue
            name = m.group(1).lower()
            if any(name.startswith(p) for p in allowed_prefixes):
                continue
            for forbidden in forbidden_substrings:
                assert forbidden not in name, (
                    f"{py.name}:{line_no} 에 출금 관련 메서드/클래스 정의 발견 "
                    f"— CLAUDE.md §2.1.2 위반: {line.strip()!r}"
                )


# ── 4. .env.example 형식 ─────────────────────────────────────────

def test_env_example_has_no_filled_secret_values():
    """.env.example 의 모든 secret 변수는 값이 비어있어야 함."""
    secret_keys = {
        "UPBIT_ACCESS_KEY", "UPBIT_SECRET_KEY",
        "OKX_API_KEY", "OKX_API_SECRET", "OKX_API_PASSWORD",
        "ANTHROPIC_API_KEY",
        "TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID",
        "EXCHANGERATE_API_KEY",
    }
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    for line in text.splitlines():
        m = re.match(r"^([A-Z][A-Z0-9_]*)=(.*)$", line.strip())
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if key in secret_keys:
            assert val == "", \
                f".env.example 의 {key} 값이 비어있어야 함 (현재 '{val}')"


# ── 5. 정책의 한 줄 요약이 다른 안전 문서와 일치 ────────────────

def test_claude_md_aligns_with_api_key_policy():
    claude = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    # CLAUDE.md §2.1.2: "출금 권한이 부여된 API Key 사용 금지"
    assert "출금 권한" in claude
    assert "ENABLE_WITHDRAWAL" in claude


def test_safety_principles_links_or_mirrors_policy():
    sp = (REPO_ROOT / "docs" / "safety_principles.md").read_text(encoding="utf-8")
    assert "ENABLE_WITHDRAWAL" in sp


# ── 6. 정책 문서가 실제 코드를 인용 ──────────────────────────────

def test_policy_doc_references_existing_code_paths():
    """정책 문서가 거론한 모듈 파일이 실제 존재해야 한다 (drift 방지)."""
    text = POLICY.read_text(encoding="utf-8")
    referenced_files = [
        "backend/app/brokers/base.py",
        "backend/app/brokers/{upbit,okx,binance}_adapter.py",  # glob style 표기
        "backend/app/core/feature_flags.py",
        "backend/app/audit/redaction.py",
        "backend/app/audit/audit_log.py",
        "backend/app/core/config.py",
    ]
    for ref in referenced_files:
        # glob 표기는 base 경로만 매칭 검사
        if "{" in ref:
            base = ref.split("{")[0].rstrip("/")
            assert base in text or "brokers" in text, \
                f"문서가 {ref} 와 비슷한 경로를 인용하지 않음"
        else:
            assert ref in text, f"정책 문서가 {ref} 를 인용해야 함"


def test_assert_no_withdrawal_methods_helper_referenced():
    """정책이 회귀 강제 메커니즘(`assert_no_withdrawal_methods`)을 명시해야."""
    text = POLICY.read_text(encoding="utf-8")
    assert "assert_no_withdrawal_methods" in text


def test_redaction_module_referenced():
    text = POLICY.read_text(encoding="utf-8")
    assert "redaction" in text.lower()
