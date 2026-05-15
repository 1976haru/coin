/** KillSwitchButton — 체크리스트 #50 + #75. 긴급 정지 토글. */
import { useState } from "react";
import { setKillSwitch } from "../api/risk";
import { useAdminToken } from "../contexts/AdminTokenContext";

export default function KillSwitchButton() {
  const { token, hasToken } = useAdminToken();
  const [active, setActive] = useState(false);
  const [reason, setReason] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function toggle() {
    if (!hasToken) {
      setError("admin 토큰이 필요합니다 (헤더 우측에서 로그인)");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const next = !active;
      await setKillSwitch({ active: next, reason: next ? reason : "해제" }, token);
      setActive(next);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className={active ? "card card-warn" : "card"}>
      <h2>긴급 제어 (Kill Switch)</h2>
      <button
        className={active ? "kill-btn off" : "kill-btn"}
        onClick={toggle}
        disabled={loading}
      >
        {loading
          ? "처리중…"
          : active
          ? "Kill Switch 활성 — 클릭하여 해제"
          : "Kill Switch 활성화"}
      </button>
      <p className="muted small">신규 진입 즉시 차단</p>
      <label>차단 사유 (활성화 시)</label>
      <input
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        placeholder="예: 시장 급변"
        disabled={active || loading}
      />
      {!hasToken && (
        <p className="muted small">⚠ admin 토큰 필요</p>
      )}
      {error && <p className="error-text">{error}</p>}
    </section>
  );
}
