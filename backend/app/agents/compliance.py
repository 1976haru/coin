"""ComplianceAgent — 체크리스트 #46 Compliance Agent.

CLAUDE.md 안전 원칙을 자동 점검. 운영자가 LIVE 모드 활성화 전 / 정기 audit 시
호출해 전체 시스템 컴플라이언스를 확인한다.

검사 범위 (CLAUDE.md §2):
  §2.1.2  ENABLE_WITHDRAWAL 영구 false                              [fatal]
  §2.1.2  모든 어댑터에 출금 메서드 부재                            [fatal]
  §2.1.3  Settings 시크릿 redaction 동작                             [fatal]
  §2.1.5  Frontend bundle 에 secret 패턴 부재                        [fatal]
  §2.2    Feature Flags 기본 false (LIVE/AI execution/futures)       [warning]
  §2.3    AgentDecision.is_order_intent 기본 false                   [fatal]
  §2.3    RiskOfficerAgent 가 has_veto_power=True                    [fatal]
  §2.4    단일 주문 경로 — Strategy/Agent 가 brokers 직접 import 금지 [fatal]
  §3.1    Active code 가 _legacy_innogrit import 금지                [fatal]
  §28     PAPER/SIMULATION 모드에 LIVE 키 미존재                      [warning]
  §27     ADMIN_TOKEN 변경됨                                          [warning]
  §9      Settings.validate() 결과 통합                               [warning]
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable, Literal

from .base import AgentCapability


CheckSeverity = Literal["fatal", "warning", "info"]


@dataclass(frozen=True)
class ComplianceCheck:
    name: str
    passed: bool
    severity: CheckSeverity
    message: str
    rule_ref: str             # CLAUDE.md 섹션 참조 (예: "§2.1.2")

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ComplianceReport:
    total: int
    passed: int
    failed: int
    fatal_failures: int
    warning_failures: int
    checks: tuple[ComplianceCheck, ...] = field(default_factory=tuple)

    @property
    def has_fatal(self) -> bool:
        return self.fatal_failures > 0

    @property
    def all_passed(self) -> bool:
        return self.failed == 0

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "fatal_failures": self.fatal_failures,
            "warning_failures": self.warning_failures,
            "has_fatal": self.has_fatal,
            "all_passed": self.all_passed,
            "checks": [c.to_dict() for c in self.checks],
        }


# ── Agent ────────────────────────────────────────────────────────

class ComplianceAgent:
    """CLAUDE.md 안전 원칙 자동 점검 Agent."""

    capability = AgentCapability(
        name="compliance",
        role="explain",
        description=(
            "CLAUDE.md 안전 원칙 자동 점검 — 출금 메서드 부재, "
            "ENABLE_WITHDRAWAL 영구 false, AgentDecision is_order_intent, "
            "단일 주문 경로, 시크릿 redaction 등."
        ),
        has_veto_power=False,
        is_deterministic=True,
        requires_llm=False,
        inputs=("settings",),
    )

    # ── 핵심 audit ────────────────────────────────────────────────

    def audit(
        self,
        *,
        settings: Any | None = None,
        repo_root: Path | None = None,
    ) -> ComplianceReport:
        """전체 컴플라이언스 점검을 수행하고 ComplianceReport 반환."""
        repo_root = repo_root or self._default_repo_root()
        checks: list[ComplianceCheck] = []

        checks.append(self._check_enable_withdrawal())
        checks.append(self._check_no_withdrawal_methods())
        checks.append(self._check_redaction())
        checks.append(self._check_agent_decision_default())
        checks.append(self._check_risk_officer_veto())
        checks.append(self._check_strategy_module_boundaries(repo_root))
        checks.append(self._check_active_code_no_legacy(repo_root))
        checks.append(self._check_frontend_no_secrets(repo_root))
        checks.append(self._check_feature_flag_defaults())

        if settings is not None:
            checks.extend(self._checks_from_settings(settings))

        passed = sum(1 for c in checks if c.passed)
        failed = sum(1 for c in checks if not c.passed)
        fatal_f = sum(1 for c in checks if not c.passed and c.severity == "fatal")
        warn_f = sum(1 for c in checks if not c.passed and c.severity == "warning")
        return ComplianceReport(
            total=len(checks),
            passed=passed,
            failed=failed,
            fatal_failures=fatal_f,
            warning_failures=warn_f,
            checks=tuple(checks),
        )

    def render_text(self, report: ComplianceReport, *, format: str = "markdown") -> str:
        if format == "markdown":
            mark = "🟢" if report.all_passed else ("🔴" if report.has_fatal else "🟡")
            lines = [
                f"## {mark} 컴플라이언스 점검 결과",
                f"- 전체: {report.total} / 통과: {report.passed} / 실패: {report.failed}",
                f"- Fatal: {report.fatal_failures} / Warning: {report.warning_failures}",
                "",
            ]
            # 실패 우선
            failed_checks = [c for c in report.checks if not c.passed]
            passed_checks = [c for c in report.checks if c.passed]
            if failed_checks:
                lines.append("### 실패 항목")
                for c in failed_checks:
                    icon = "❌" if c.severity == "fatal" else "⚠️"
                    lines.append(f"- {icon} **`{c.name}`** ({c.rule_ref}): {c.message}")
                lines.append("")
            if passed_checks:
                lines.append("### 통과 항목")
                for c in passed_checks:
                    lines.append(f"- ✅ `{c.name}` ({c.rule_ref})")
            return "\n".join(lines)

        # plain
        lines = [
            f"=== 컴플라이언스 점검 ===",
            f"  total={report.total} passed={report.passed} failed={report.failed}",
            f"  fatal={report.fatal_failures} warning={report.warning_failures}",
            "",
        ]
        for c in report.checks:
            mark = "[OK]" if c.passed else (
                "[FAIL]" if c.severity == "fatal" else "[WARN]"
            )
            lines.append(f"  {mark} {c.name} ({c.rule_ref})")
            if not c.passed:
                lines.append(f"        {c.message}")
        return "\n".join(lines)

    # ── AgentBase contract ────────────────────────────────────────

    def decide(self, input_signal: dict, context: dict | None = None) -> Any:
        from .orchestrator import AgentDecision
        ctx = context or {}
        settings = ctx.get("settings")
        report = self.audit(settings=settings)
        action = "HOLD"
        reason = (
            f"ComplianceAgent: {'통과' if report.all_passed else '위반 ' + str(report.failed) + '건'}"
        )
        return AgentDecision(
            action, 0.0, reason,
            explain_text=self.render_text(report, format="markdown"),
        )

    # ── 개별 check 구현 ───────────────────────────────────────────

    @staticmethod
    def _default_repo_root() -> Path:
        """본 모듈의 위치에서 repo root 추정."""
        # backend/app/agents/compliance.py → repo root 는 3단계 위
        return Path(__file__).resolve().parents[3]

    @staticmethod
    def _check_enable_withdrawal() -> ComplianceCheck:
        """ENABLE_WITHDRAWAL 가 환경변수 무관 영구 false."""
        try:
            from app.core.feature_flags import FeatureFlags
            f = FeatureFlags()
            return ComplianceCheck(
                name="enable_withdrawal_permanently_false",
                passed=(f.enable_withdrawal is False),
                severity="fatal",
                message=(
                    "OK" if not f.enable_withdrawal
                    else f"FeatureFlags.enable_withdrawal == {f.enable_withdrawal} (반드시 False)"
                ),
                rule_ref="§2.1.2",
            )
        except Exception as e:
            return ComplianceCheck(
                name="enable_withdrawal_permanently_false",
                passed=False, severity="fatal",
                message=f"검사 실행 실패: {e}",
                rule_ref="§2.1.2",
            )

    @staticmethod
    def _check_no_withdrawal_methods() -> ComplianceCheck:
        try:
            from app.brokers import (
                ExchangeAdapter, MockExchangeAdapter,
                UpbitAdapter, OkxAdapter, BinanceAdapter,
                PaperBroker, assert_no_withdrawal_methods,
            )
            for cls in (ExchangeAdapter, MockExchangeAdapter,
                        UpbitAdapter, OkxAdapter, BinanceAdapter, PaperBroker):
                assert_no_withdrawal_methods(cls)
            return ComplianceCheck(
                name="adapters_no_withdrawal_methods",
                passed=True, severity="fatal",
                message="모든 어댑터에 출금 메서드 부재",
                rule_ref="§2.1.2",
            )
        except AssertionError as e:
            return ComplianceCheck(
                name="adapters_no_withdrawal_methods",
                passed=False, severity="fatal",
                message=str(e),
                rule_ref="§2.1.2",
            )
        except Exception as e:
            return ComplianceCheck(
                name="adapters_no_withdrawal_methods",
                passed=False, severity="fatal",
                message=f"검사 실행 실패: {e}",
                rule_ref="§2.1.2",
            )

    @staticmethod
    def _check_redaction() -> ComplianceCheck:
        """app.audit.redaction 가 secret 키를 마스킹."""
        try:
            from app.audit.redaction import redact, REDACTED
            sample = {"api_key": "leak", "symbol": "BTC"}
            out = redact(sample)
            ok = (out["api_key"] == REDACTED and out["symbol"] == "BTC")
            return ComplianceCheck(
                name="audit_redaction_active",
                passed=ok, severity="fatal",
                message="OK" if ok else "redact() 가 api_key 를 마스킹하지 않음",
                rule_ref="§2.1.3",
            )
        except Exception as e:
            return ComplianceCheck(
                name="audit_redaction_active",
                passed=False, severity="fatal",
                message=f"검사 실행 실패: {e}",
                rule_ref="§2.1.3",
            )

    @staticmethod
    def _check_agent_decision_default() -> ComplianceCheck:
        """AgentDecision 기본 is_order_intent=False."""
        try:
            from app.agents.orchestrator import AgentDecision
            d = AgentDecision("HOLD", 0.0, "test")
            ok = d.is_order_intent is False
            return ComplianceCheck(
                name="agent_decision_is_order_intent_default_false",
                passed=ok, severity="fatal",
                message="OK" if ok else "AgentDecision.is_order_intent 기본값이 False 가 아님",
                rule_ref="§2.3",
            )
        except Exception as e:
            return ComplianceCheck(
                name="agent_decision_is_order_intent_default_false",
                passed=False, severity="fatal",
                message=f"검사 실행 실패: {e}",
                rule_ref="§2.3",
            )

    @staticmethod
    def _check_risk_officer_veto() -> ComplianceCheck:
        try:
            from app.agents.risk_officer import RiskOfficerAgent
            cap = RiskOfficerAgent.capability
            return ComplianceCheck(
                name="risk_officer_has_veto_power",
                passed=cap.has_veto_power is True,
                severity="fatal",
                message=("OK" if cap.has_veto_power
                         else "RiskOfficerAgent.capability.has_veto_power 가 False"),
                rule_ref="§2.3",
            )
        except Exception as e:
            return ComplianceCheck(
                name="risk_officer_has_veto_power",
                passed=False, severity="fatal",
                message=f"검사 실행 실패: {e}",
                rule_ref="§2.3",
            )

    @staticmethod
    def _check_strategy_module_boundaries(repo_root: Path) -> ComplianceCheck:
        """app.strategies.* 가 app.brokers / app.execution 을 직접 import 금지."""
        violations = []
        strategies_dir = repo_root / "backend" / "app" / "strategies"
        if not strategies_dir.exists():
            return ComplianceCheck(
                name="strategies_module_boundary",
                passed=True, severity="fatal",
                message=f"디렉토리 없음 (skip): {strategies_dir}",
                rule_ref="§3.1",
            )
        for py in strategies_dir.rglob("*.py"):
            text = py.read_text(encoding="utf-8", errors="ignore")
            for line in text.splitlines():
                s = line.strip()
                if s.startswith("import ") or s.startswith("from "):
                    if "app.brokers" in s or "app.execution" in s:
                        violations.append(f"{py.name}: {s}")
                        break
        return ComplianceCheck(
            name="strategies_module_boundary",
            passed=not violations,
            severity="fatal",
            message=("OK" if not violations
                     else f"전략이 brokers/execution import: {violations}"),
            rule_ref="§3.1",
        )

    @staticmethod
    def _check_active_code_no_legacy(repo_root: Path) -> ComplianceCheck:
        """active app/ 에 _legacy_innogrit / utils.* import 금지.
        ccxt/pyupbit 는 brokers/{upbit,okx,binance}_adapter.py 만 허용.
        """
        violations = []
        ALLOWED_FOR_EXCHANGE_SDK = {
            "upbit_adapter.py", "okx_adapter.py", "binance_adapter.py",
        }
        FORBIDDEN_LEGACY = (
            "_legacy_innogrit", "from utils.", "import utils.",
        )
        FORBIDDEN_EXCHANGE_SDK = ("import pyupbit", "import ccxt",
                                   "from pyupbit", "from ccxt")

        app_dir = repo_root / "backend" / "app"
        if not app_dir.exists():
            return ComplianceCheck(
                name="active_code_no_legacy_imports",
                passed=True, severity="fatal",
                message=f"디렉토리 없음 (skip): {app_dir}",
                rule_ref="§3.1",
            )
        for py in app_dir.rglob("*.py"):
            text = py.read_text(encoding="utf-8", errors="ignore")
            for line in text.splitlines():
                s = line.strip()
                if not (s.startswith("import ") or s.startswith("from ")):
                    continue
                for token in FORBIDDEN_LEGACY:
                    if token in s:
                        violations.append(f"{py.name}: {s}")
                        break
                for token in FORBIDDEN_EXCHANGE_SDK:
                    if token in s and py.name not in ALLOWED_FOR_EXCHANGE_SDK:
                        violations.append(f"{py.name}: {s}")
                        break

        return ComplianceCheck(
            name="active_code_no_legacy_imports",
            passed=not violations,
            severity="fatal",
            message="OK" if not violations
                    else f"위반: {violations[:5]}{'...' if len(violations) > 5 else ''}",
            rule_ref="§3.1",
        )

    @staticmethod
    def _check_frontend_no_secrets(repo_root: Path) -> ComplianceCheck:
        """frontend/src 에 secret 패턴 부재."""
        forbidden = (
            "ANTHROPIC_API_KEY", "OKX_API_SECRET", "UPBIT_SECRET_KEY",
            "TELEGRAM_TOKEN", "ADMIN_TOKEN=",
        )
        violations = []
        src = repo_root / "frontend" / "src"
        if not src.exists():
            return ComplianceCheck(
                name="frontend_no_secrets",
                passed=True, severity="fatal",
                message=f"디렉토리 없음 (skip): {src}",
                rule_ref="§2.1.5",
            )
        for path in src.rglob("*"):
            if path.is_file() and path.suffix in {
                ".ts", ".tsx", ".js", ".jsx", ".json", ".html", ".css",
            }:
                text = path.read_text(encoding="utf-8", errors="ignore")
                for token in forbidden:
                    if token in text:
                        violations.append(f"{path.name}: {token}")
        return ComplianceCheck(
            name="frontend_no_secrets",
            passed=not violations,
            severity="fatal",
            message="OK" if not violations
                    else f"frontend src 에 secret 패턴: {violations}",
            rule_ref="§2.1.5",
        )

    @staticmethod
    def _check_feature_flag_defaults() -> ComplianceCheck:
        """위험 ENABLE_* 플래그 기본 false 확인 (env 미설정 상태 가정)."""
        try:
            import os
            # 환경변수 일시 제거 + 평가
            saved = {}
            for k in ("ENABLE_LIVE_TRADING", "ENABLE_AI_EXECUTION",
                      "ENABLE_CRYPTO_FUTURES_LIVE",
                      "ENABLE_LIVE_ORDER_SUBMISSION", "ENABLE_AI_AGENTS"):
                if k in os.environ:
                    saved[k] = os.environ.pop(k)
            try:
                from app.core.feature_flags import FeatureFlags
                f = FeatureFlags()
                violations = []
                if f.enable_live_trading:           violations.append("ENABLE_LIVE_TRADING")
                if f.enable_ai_execution:           violations.append("ENABLE_AI_EXECUTION")
                if f.enable_crypto_futures_live:    violations.append("ENABLE_CRYPTO_FUTURES_LIVE")
                if f.enable_live_order_submission:  violations.append("ENABLE_LIVE_ORDER_SUBMISSION")
                if f.enable_ai_agents:              violations.append("ENABLE_AI_AGENTS")
            finally:
                os.environ.update(saved)
            return ComplianceCheck(
                name="feature_flags_default_false",
                passed=not violations,
                severity="warning",
                message="OK" if not violations
                        else f"기본 true 인 위험 플래그: {violations}",
                rule_ref="§2.2",
            )
        except Exception as e:
            return ComplianceCheck(
                name="feature_flags_default_false",
                passed=False, severity="warning",
                message=f"검사 실행 실패: {e}",
                rule_ref="§2.2",
            )

    @staticmethod
    def _checks_from_settings(settings: Any) -> Iterable[ComplianceCheck]:
        """Settings.validate() 결과를 컴플라이언스 체크로 변환."""
        try:
            warnings = settings.validate() or []
        except Exception as e:
            yield ComplianceCheck(
                name="settings_validate",
                passed=False, severity="warning",
                message=f"Settings.validate() 실패: {e}",
                rule_ref="§9",
            )
            return

        if not warnings:
            yield ComplianceCheck(
                name="settings_validate",
                passed=True, severity="warning",
                message="OK — 운영 경고 없음",
                rule_ref="§9",
            )
            return

        # 각 경고를 별도 check 로
        for i, w in enumerate(warnings):
            yield ComplianceCheck(
                name=f"settings_warning_{i + 1}",
                passed=False, severity="warning",
                message=w,
                rule_ref="§9 / §27 / §28",
            )
