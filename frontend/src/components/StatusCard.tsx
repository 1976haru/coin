/** 시스템 상태 카드 — 체크리스트 #7 dashboard 스켈레톤. */
import { useEffect, useState } from "react";
import { fetchFreshness, type FreshnessStatus } from "../api/health";

export default function StatusCard() {
  const [fresh, setFresh] = useState<FreshnessStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const tick = () => {
      fetchFreshness()
        .then((d) => !cancelled && (setFresh(d), setError(null)))
        .catch((e) => !cancelled && setError(String(e)));
    };
    tick();
    const id = setInterval(tick, 5000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  return (
    <section className={`card ${fresh?.ok ? "card-ok" : "card-warn"}`}>
      <h2>시스템 상태</h2>
      {error && <div className="error-text">{error}</div>}
      {fresh && (
        <ul className="status-list">
          <li>데이터 freshness: <strong>{fresh.ok ? "OK" : "STALE"}</strong></li>
          <li>이유: {fresh.reason}</li>
          {fresh.source && <li>소스: {fresh.source}</li>}
          {fresh.age_sec !== undefined && <li>지연: {fresh.age_sec.toFixed(2)}s</li>}
        </ul>
      )}
    </section>
  );
}
