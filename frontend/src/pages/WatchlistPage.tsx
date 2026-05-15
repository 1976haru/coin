/** Watchlist 페이지 — 체크리스트 #7 + #14. 읽기 전용 표시 (변경은 admin 토큰). */
import { useEffect, useState } from "react";
import { fetchWatchlist, type WatchlistEntry } from "../api/watchlist";

export default function WatchlistPage() {
  const [entries, setEntries] = useState<WatchlistEntry[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchWatchlist()
      .then((d) => setEntries(d.entries))
      .catch((e) => setError(String(e)));
  }, []);

  if (error) return <div className="error-text">{error}</div>;

  return (
    <section className="card">
      <h2>Watchlist ({entries.length})</h2>
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
