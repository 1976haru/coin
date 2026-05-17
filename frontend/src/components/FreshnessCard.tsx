/** 시스템 freshness 카드 — 체크리스트 #7.
 *
 * 본래 `StatusCard.tsx` 였으나, 스펙(체크리스트 #7)이 StatusCard 라는 이름을
 * `{title, value, description, tone}` props 를 받는 범용 카드로 재정의했기에
 * 본 freshness-전용 카드는 FreshnessCard 로 분리했다. (#73 위젯에서 계속 사용.)
 */
import { useEffect, useState } from "react";
import { fetchFreshness, type FreshnessStatus } from "../api/health";

export default function FreshnessCard() {
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
