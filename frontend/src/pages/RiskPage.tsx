/** RiskPage — 체크리스트 #75 Risk Panel. 시스템 리스크 상태 + 긴급 제어. */
import { useEffect, useState } from "react";
import KillSwitchButton from "../components/KillSwitchButton";
import { fetchStatus, type AppStatus } from "../api/health";
import { fetchPaperGate, fetchShadowGate, type PromotionGateMetrics } from "../api/risk";

interface SystemStatus extends AppStatus {
  risk_status?: {
    kill_switch: boolean;
    daily_pnl_pct: number;
    consecutive_losses: number;
    daily_loss_limit_pct: number;
  };
  pending_approvals?: number;
  audit_events?: number;
  safety_warnings?: string[];
}

export default function RiskPage() {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [paperGate, setPaperGate] = useState<PromotionGateMetrics | null>(null);
  const [shadowGate, setShadowGate] = useState<PromotionGateMetrics | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const [s, p, sh] = await Promise.all([
          fetchStatus() as Promise<SystemStatus>,
          fetchPaperGate().catch(() => null),
          fetchShadowGate().catch(() => null),
        ]);
        if (cancelled) return;
        setStatus(s);
        setPaperGate(p);
        setShadowGate(sh);
        setError(null);
      } catch (e) {
        if (!cancelled) setError(String(e));
      }
    }
    void load();
    const id = setInterval(load, 5000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  return (
    <div className="grid">
      <KillSwitchButton />

      <section className="card">
        <h2>시스템 상태</h2>
        {error && <p className="error-text">{error}</p>}
        {status && (
          <ul className="status-list">
            <li>운용 모드: <strong>{status.trading_mode}</strong></li>
            <li>Kill Switch: <strong>{status.risk_status?.kill_switch ? "🔴 ON" : "🟢 OFF"}</strong></li>
            <li>일 PnL: <strong>{status.risk_status?.daily_pnl_pct?.toFixed(2)}%</strong></li>
            <li>연속 손실: <strong>{status.risk_status?.consecutive_losses ?? 0}</strong></li>
            <li>일 손실 한도: {status.risk_status?.daily_loss_limit_pct ?? 0}%</li>
            <li>승인 대기: {status.pending_approvals ?? 0}</li>
            <li>감사 이벤트: {status.audit_events ?? 0}</li>
          </ul>
        )}
      </section>

      {status?.safety_warnings && status.safety_warnings.length > 0 && (
        <section className="card card-warn">
          <h2>⚠ 운영 경고 (#9 Settings.validate)</h2>
          <ul className="warning-list">
            {status.safety_warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </section>
      )}

      <section className="card">
        <h2>승격 게이트</h2>
        {paperGate && (
          <div className="gate-row">
            <span className={`gate-mark ${paperGate.passed ? "pass" : "fail"}`}>
              {paperGate.passed ? "✓" : "✗"}
            </span>
            <strong>PAPER → LIVE_SHADOW</strong>
            <span className="muted small">{paperGate.reason}</span>
          </div>
        )}
        {shadowGate && (
          <div className="gate-row">
            <span className={`gate-mark ${shadowGate.passed ? "pass" : "fail"}`}>
              {shadowGate.passed ? "✓" : "✗"}
            </span>
            <strong>LIVE_SHADOW → LIVE_MANUAL</strong>
            <span className="muted small">{shadowGate.reason}</span>
          </div>
        )}
      </section>
    </div>
  );
}
