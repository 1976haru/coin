"""scripts/mvp_gate.py — 체크리스트 #90 MVP Gate.

MVP 출시 전 마지막 게이트. Pre-market checklist (#91) + 추가 MVP 요구사항 검증.

추가 MVP 요구사항:
  - 단위 테스트 1000+개 통과 (현재 1274 기준)
  - ComplianceAgent fatal 0
  - 핵심 문서 존재 (CLAUDE.md, safety_principles, api_key_policy, sandbox_paper_keys)
  - 진척도 문서 (checklist_progress.md) 존재
  - frontend dist 빌드 결과물 존재

종료 코드:
    0  MVP 게이트 통과
    1  통과 못함

사용:
    python scripts/mvp_gate.py
    python scripts/mvp_gate.py --json
"""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

_HERE = Path(os.path.abspath(__file__)).parent
_ROOT = _HERE.parent
_BACKEND = _ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.agents.compliance import ComplianceAgent  # noqa: E402
from app.core.config import get_settings  # noqa: E402


# 핵심 문서 — 누락 시 MVP 출시 불가
REQUIRED_DOCS = [
    "CLAUDE.md",
    "docs/safety_principles.md",
    "docs/api_key_policy.md",
    "docs/sandbox_paper_keys.md",
    "docs/checklist_progress.md",
    "docs/architecture.md",
    "docs/operating_modes.md",
    "docs/strategy_portfolio.md",
    "docs/product_scope.md",
]

MIN_TEST_COUNT = 1000


def _check_docs(root: Path) -> tuple[bool, list[str]]:
    missing = []
    for doc in REQUIRED_DOCS:
        p = root / doc
        if not p.is_file():
            missing.append(doc)
        elif p.stat().st_size < 100:
            missing.append(f"{doc} (too small)")
    return not missing, missing


def _check_tests(backend: Path) -> tuple[bool, str, int]:
    """pytest 실행해 통과 수 확인."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q",
         "--no-header", "--no-summary", "-p", "no:warnings"],
        cwd=str(backend),
        capture_output=True, text=True, timeout=120,
    )
    out = result.stdout + result.stderr
    # "1274 passed" 패턴 찾기
    import re
    m = re.search(r"(\d+) passed", out)
    n = int(m.group(1)) if m else 0
    passed = result.returncode == 0 and n >= MIN_TEST_COUNT
    return passed, f"{n} passed (min {MIN_TEST_COUNT})", n


def _check_frontend_dist(root: Path) -> tuple[bool, str]:
    dist = root / "frontend" / "dist"
    if not dist.is_dir():
        return False, "frontend/dist 없음 — `cd frontend && npm run build` 필요"
    index = dist / "index.html"
    if not index.is_file():
        return False, "frontend/dist/index.html 누락"
    return True, "OK"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="MVP 출시 게이트")
    p.add_argument("--json", dest="as_json", action="store_true")
    p.add_argument("--skip-tests", action="store_true",
                   help="pytest 실행 생략 (이미 CI 통과 시)")
    args = p.parse_args(argv)

    settings = get_settings()
    checks: list[dict] = []

    # 1. ComplianceAgent
    report = ComplianceAgent().audit(settings=settings)
    checks.append({
        "name": "compliance",
        "passed": report.fatal_failures == 0,
        "detail": f"{report.passed}/{report.total} 통과 — Fatal {report.fatal_failures} / Warning {report.warning_failures}",
    })

    # 2. 핵심 문서
    docs_ok, missing = _check_docs(_ROOT)
    checks.append({
        "name": "required_docs",
        "passed": docs_ok,
        "detail": "OK" if docs_ok else f"누락: {missing}",
    })

    # 3. Frontend dist
    fe_ok, fe_msg = _check_frontend_dist(_ROOT)
    checks.append({"name": "frontend_dist", "passed": fe_ok, "detail": fe_msg})

    # 4. 단위 테스트 (옵션)
    if not args.skip_tests:
        try:
            tests_ok, tests_msg, _ = _check_tests(_BACKEND)
        except subprocess.TimeoutExpired:
            tests_ok, tests_msg = False, "pytest timeout"
        checks.append({"name": "unit_tests", "passed": tests_ok, "detail": tests_msg})

    overall = all(c["passed"] for c in checks)

    if args.as_json:
        print(json.dumps({
            "overall_pass": overall,
            "trading_mode": settings.trading_mode.value,
            "checks": checks,
        }, ensure_ascii=False, indent=2))
    else:
        print("══ MVP 출시 게이트 " + "═" * 50)
        print(f"운용 모드: {settings.trading_mode.value}\n")
        for c in checks:
            mark = "✅" if c["passed"] else "❌"
            print(f"  {mark} {c['name']:20} {c['detail']}")
        print()
        if overall:
            print("🟢 MVP 게이트 통과 — 출시 가능")
        else:
            failed = [c["name"] for c in checks if not c["passed"]]
            print(f"🔴 게이트 차단 — 실패 항목: {failed}")
            print("   해결 후 재실행하세요.")

    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
