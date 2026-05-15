"""scripts/pre_market_checklist.py — 체크리스트 #91 Pre-market Checklist.

운영 시작 전 또는 LIVE 모드 활성화 직전에 실행하는 자동 점검 CLI.
ComplianceAgent (#46) + Data Quality (#17) + Settings.validate (#9) + 모드 sanity 통합.

종료 코드:
    0  모든 fatal 통과 + warning 경미
    1  warning 발견 (운영자 확인 권장)
    2  fatal 위반 (LIVE 활성화 금지)
    3  스크립트 실행 오류

사용:
    cd cointrade
    python scripts/pre_market_checklist.py
    python scripts/pre_market_checklist.py --json
    python scripts/pre_market_checklist.py --fail-on-warning
"""
from __future__ import annotations
import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_BACKEND = os.path.join(_ROOT, "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from app.agents.compliance import ComplianceAgent  # noqa: E402
from app.core.config import get_settings  # noqa: E402


def _section(title: str) -> str:
    return f"\n══ {title} " + "═" * (60 - len(title))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Pre-market checklist")
    p.add_argument("--json", dest="as_json", action="store_true")
    p.add_argument("--fail-on-warning", action="store_true",
                   help="warning 발견 시에도 exit code 1")
    args = p.parse_args(argv)

    settings = get_settings()
    agent = ComplianceAgent()
    report = agent.audit(settings=settings)

    fatal_count = report.fatal_failures
    warning_count = report.warning_failures
    overall_pass = report.all_passed

    if args.as_json:
        print(json.dumps({
            "overall_pass": overall_pass,
            "fatal_failures": fatal_count,
            "warning_failures": warning_count,
            "report": report.to_dict(),
            "trading_mode": settings.trading_mode.value,
        }, ensure_ascii=False, indent=2))
    else:
        print(_section("Pre-market Checklist"))
        print(f"Trading mode : {settings.trading_mode.value}")
        print(f"전체 검사    : {report.total}")
        print(f"통과         : {report.passed}")
        print(f"Fatal 실패   : {fatal_count}")
        print(f"Warning 실패 : {warning_count}")
        print()

        # 실패 항목
        for c in report.checks:
            if c.passed:
                continue
            mark = "✗" if c.severity == "fatal" else "⚠"
            print(f"  {mark} [{c.severity}] {c.name} ({c.rule_ref})")
            print(f"      {c.message}")

        # 통과 항목 요약
        passed_count = sum(1 for c in report.checks if c.passed)
        if passed_count > 0:
            print(f"\n  ✓ {passed_count}개 검사 통과")

        # 결론
        print()
        if overall_pass:
            print("✅ 모든 검사 통과 — 운영 시작 가능")
        elif fatal_count > 0:
            print(f"🔴 Fatal {fatal_count}건 — 즉시 해결 필요. LIVE 활성화 금지.")
        else:
            print(f"🟡 Warning {warning_count}건 — 운영자 확인 권장.")

    if fatal_count > 0:
        return 2
    if warning_count > 0 and args.fail_on_warning:
        return 1
    if warning_count > 0:
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        print(f"스크립트 오류: {e}", file=sys.stderr)
        sys.exit(3)
