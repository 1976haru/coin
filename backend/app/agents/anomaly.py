"""AnomalyAgent — 체크리스트 #37 + #40 (Anomaly Agent boosted).

이상 탐지 Agent. context 의 다양한 신호를 종합해 거래 차단 여부 결정.
결정론적 — LLM 사용 안 함. 차단 시 ``risk_veto=True``.

체크리스트 #40 보강 항목:
  - QualityReport (#17) — has_blocking 시 차단
  - NoticeRegistry SymbolNoticeStatus (#18) — !tradable 또는 !deposit_withdrawal_ok 시 차단
  - news_severity (#19) — "block" 시 차단 (SignalQuality 의 -30 페널티와 별개로 hard veto)
  - kimp_anomaly_hint — 김프율 ±10% 초과 hint (#34/#35) 시 차단

CLAUDE.md §2.3: AgentDecision.is_order_intent 기본 False.
"""
from __future__ import annotations
from typing import Any

from .base import AgentCapability


class AnomalyAgent:
    """이상 데이터/시장 상태 감지 시 거래 차단 (hard veto).

    입력 context 키 (모두 옵션):
      - anomaly                : 외부에서 미리 결정된 이상 플래그
      - data_quality_alarm     : True 면 차단
      - freshness_stale        : True 면 차단 (#16 freshness 와 연동)
      - quality_report         : QualityReport 객체/dict — has_blocking 시 차단 (#17)
      - notice_status          : SymbolNoticeStatus 객체/dict — !tradable / !dwd 시 차단 (#18)
      - news_severity          : "block" 시 차단 (#19)
      - kimp_anomaly_hint      : True 면 차단 (#34/#35)
    """

    capability = AgentCapability(
        name="anomaly",
        role="anomaly",
        description=(
            "이상 데이터/시장 상태 감지 시 hard veto. "
            "Quality(#17) / Notices(#18) / News(#19) / Kimp(#34) 통합."
        ),
        has_veto_power=True,
        is_deterministic=True,
        requires_llm=False,
        inputs=(
            "anomaly", "data_quality_alarm", "freshness_stale",
            "quality_report", "notice_status",
            "news_severity", "kimp_anomaly_hint",
        ),
    )

    def decide(self, input_signal: dict, context: dict | None = None) -> Any:
        from .orchestrator import AgentDecision
        ctx = context or {}

        # 1. 외부 anomaly 플래그
        if ctx.get("anomaly"):
            return self._block("이상 데이터", "이상 데이터 감지로 거래 차단")

        # 2. 데이터 품질 경보
        if ctx.get("data_quality_alarm"):
            return self._block("데이터 품질 경보", "데이터 품질 경보 (#17)")

        # 3. Freshness stale
        if ctx.get("freshness_stale"):
            return self._block("시세 stale", "시세 stale (#16)")

        # 4. QualityReport blocking checks (#17)
        qr = ctx.get("quality_report")
        if qr is not None and self._read_attr(qr, "has_blocking", default=False):
            blocks = self._read_attr(qr, "blocks", default=())
            reasons = ", ".join(
                self._read_attr(b, "reason", default="") for b in (blocks or ())
            ) or "QualityReport blocking"
            return self._block(
                f"품질 검사 차단: {reasons}",
                f"QualityReport 차단 사유: {reasons}",
            )

        # 5. NoticeRegistry SymbolNoticeStatus (#18)
        ns = ctx.get("notice_status")
        if ns is not None:
            tradable = self._read_attr(ns, "tradable", default=True)
            if not tradable:
                return self._block(
                    "거래 불가 — 상장폐지/점검 (#18)",
                    "NoticeRegistry: 상장폐지 또는 점검",
                )
            dwd = self._read_attr(ns, "deposit_withdrawal_ok", default=True)
            if not dwd:
                return self._block(
                    "입출금 중단 (#18)",
                    "NoticeRegistry: 입출금 불가",
                )

        # 6. news_severity == "block" (#19) — hard veto
        # SignalQualityAgent 의 -30 페널티와 별개로, block 등급 뉴스는 즉시 차단.
        if ctx.get("news_severity") == "block":
            return self._block(
                "뉴스 block 등급 (#19)",
                "심각도 block 뉴스 — 거래 일시 중단",
            )

        # 7. 김프율 이상치 hint (#34/#35)
        if ctx.get("kimp_anomaly_hint"):
            return self._block(
                "김프율 이상치 (#34)",
                "김프율 정상 범위 밖 — FX/거래소 장애 의심",
            )

        # 통과
        return AgentDecision(
            input_signal.get("action", "HOLD"),
            float(input_signal.get("confidence", 0.0)),
            "AnomalyAgent: 정상",
            quality_score=0.0,
            risk_veto=False,
            explain_text="이상 신호 없음",
        )

    # ── 헬퍼 ──────────────────────────────────────────────────────

    def _block(self, reason_short: str, explain: str):
        from .orchestrator import AgentDecision
        return AgentDecision(
            "HOLD", 0.0,
            f"AnomalyAgent veto: {reason_short}",
            quality_score=0.0,
            risk_veto=True,
            explain_text=explain,
        )

    @staticmethod
    def _read_attr(obj: Any, name: str, *, default=None) -> Any:
        """dict 또는 객체 모두에서 속성 읽기."""
        if isinstance(obj, dict):
            return obj.get(name, default)
        return getattr(obj, name, default)
