/** Audit 로그 페이지 — 체크리스트 #7. admin 토큰 입력 후 tail 표시. */
import { useState } from "react";
import { fetchAuditTail, type AuditEvent } from "../api/audit";

export default function AuditPage() {
  const [token, setToken] = useState("");
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function load() {
    setError(null);
    setLoading(true);
    try {
      const d = await fetchAuditTail(token);
      setEvents(d.events);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="card">
      <h2>Audit Log</h2>
      <div className="row">
        <input
          type="password"
          placeholder="admin token (X-Admin-Token)"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          aria-label="Admin token"
        />
        <button onClick={load} disabled={loading || !token}>
          {loading ? "로딩…" : "조회"}
        </button>
      </div>
      {error && <div className="error-text">{error}</div>}
      {events.length > 0 && (
        <ul className="audit-list">
          {events.map((ev, i) => (
            <li key={i}>
              <span className="ts">{ev.ts}</span>
              <span className="evt">{ev.event_type}</span>
              <pre>{JSON.stringify(ev.payload, null, 2)}</pre>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
