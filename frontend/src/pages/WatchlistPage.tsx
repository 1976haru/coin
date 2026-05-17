/** Watchlist 페이지 — 체크리스트 #7 + #14. 읽기 전용 표시 (변경은 admin 토큰). */
import { useEffect, useState } from "react";
import {
  fetchWatchlist,
  type WatchlistEntry,
  type WatchlistSummary,
} from "../api/watchlist";

export default function WatchlistPage() {
  const [entries, setEntries] = useState<WatchlistEntry[]>([]);
  const [summary, setSummary] = useState<WatchlistSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchWatchlist()
      .then((d) => {
        setEntries(d.entries);
        setSummary(d.summary ?? null);
      })
      .catch((e) => setError(String(e)));
  }, []);

  if (error) return <div className="error-text">{error}</div>;

  return (
    <section className="card">
      <h2>
        Watchlist ({entries.length}
        {summary ? ` · enabled ${summary.enabled} / disabled ${summary.disabled}` : ""})
      </h2>

      {summary && (
        <div className="watchlist-summary">
          <p className="muted" style={{ marginBottom: "0.5rem" }}>
            Watchlist 는 <strong>후보 universe 제한 장치</strong>입니다 — 주문 허용
            목록이 아닙니다. 여기 있어도 RiskManager / OrderGuard / PermissionGate
            를 통과해야 주문됩니다.
          </p>
          <table className="data-table" style={{ marginBottom: "0.5rem" }}>
            <thead>
              <tr><th>list_name</th><th>enabled</th><th>limit</th></tr>
            </thead>
            <tbody>
              {Object.keys(summary.limits)
                .filter((k) => k !== "max_enabled_total" && k !== "other")
                .map((lname) => (
                  <tr key={lname}>
                    <td>{lname}</td>
                    <td>{summary.by_list_name[lname] ?? 0}</td>
                    <td>{summary.limits[lname]}</td>
                  </tr>
                ))}
              <tr>
                <td><strong>(total enabled)</strong></td>
                <td>{summary.enabled}</td>
                <td>{summary.limits.max_enabled_total}</td>
              </tr>
            </tbody>
          </table>
          <p className="muted">
            by_exchange:{" "}
            {Object.entries(summary.by_exchange)
              .map(([k, v]) => `${k}=${v}`)
              .join(", ") || "—"}
          </p>
        </div>
      )}

      {entries.length === 0 ? (
        <p className="muted">등록된 항목이 없습니다. POST /api/watchlist (admin) 으로 추가.</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>list</th><th>symbol</th><th>exchange</th><th>enabled</th><th>tags</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((e) => (
              <tr key={e.id}>
                <td>{e.list_name}</td>
                <td>{e.symbol}</td>
                <td>{e.exchange}</td>
                <td>{e.enabled ? "✓" : "—"}</td>
                <td>{e.tags.join(", ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
