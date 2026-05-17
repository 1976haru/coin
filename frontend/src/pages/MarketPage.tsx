/** Market 페이지 — 체크리스트 #15. 읽기 전용.
 *
 * 표시 항목:
 *   - 수집기 상태 (sources / 마지막 수집 시각 / 성공·실패 / 모드)
 *   - 현재 캐시된 ticker 목록
 *
 * 수동 collect 트리거 버튼은 만들지 않는다 — 본 페이지는 read-only.
 * 수집 자체는 운영자가 CLI/POST 로 트리거하거나 백그라운드 루프가 호출한다.
 */
import { useEffect, useState } from "react";
import {
  fetchTickers,
  fetchCollectorStatus,
  type TickerRow,
  type CollectorStatus,
} from "../api/market";

export default function MarketPage() {
  const [tickers, setTickers] = useState<TickerRow[]>([]);
  const [status, setStatus] = useState<CollectorStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([fetchTickers(), fetchCollectorStatus()])
      .then(([t, s]) => {
        setTickers(t.tickers);
        setStatus(s);
      })
      .catch((e) => setError(String(e)));
  }, []);

  if (error) return <div className="error-text">{error}</div>;

  return (
    <section className="card">
      <h2>Market Data ({tickers.length})</h2>

      <p className="muted" style={{ marginBottom: "0.5rem" }}>
        Read-only 시장 데이터 — Watchlist universe 기반으로만 수집됩니다.
        전체 시장 자동 스캔은 하지 않으며, 본 페이지는 조회 전용입니다.
      </p>

      {status && (
        <table className="data-table" style={{ marginBottom: "1rem" }}>
          <tbody>
            <tr><td>mode</td><td>{status.mode}</td></tr>
            <tr><td>sources</td><td>{status.sources.join(", ") || "—"}</td></tr>
            <tr><td>fx_source</td><td>{status.fx_source ?? "—"}</td></tr>
            <tr>
              <td>last_collected_at</td>
              <td>{status.last_collected_at ?? "— (아직 수집 없음)"}</td>
            </tr>
            <tr>
              <td>last_symbols (success / failure)</td>
              <td>
                {status.last_symbol_count}
                {" ("}
                <strong>{status.last_success_count}</strong>
                {" / "}
                <strong>{status.last_failure_count}</strong>
                {")"}
              </td>
            </tr>
            <tr><td>last_includes</td><td>{status.last_includes.join(", ") || "—"}</td></tr>
            <tr><td>last_list_name</td><td>{status.last_list_name ?? "—"}</td></tr>
            <tr>
              <td>freshness_threshold_sec</td>
              <td>{status.freshness_threshold_sec.toFixed(1)}</td>
            </tr>
            <tr><td>cache_size</td><td>{status.cache_size}</td></tr>
          </tbody>
        </table>
      )}

      <h3>Cached Tickers</h3>
      {tickers.length === 0 ? (
        <p className="muted">
          캐시된 ticker 가 없습니다. <code>POST /api/market/collect</code>{" "}
          (admin) 로 1회 수집을 실행하거나 백그라운드 수집 루프를 기다리세요.
        </p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>symbol</th>
              <th>exchange</th>
              <th>price</th>
              <th>bid</th>
              <th>ask</th>
              <th>spread_pct</th>
              <th>ts</th>
            </tr>
          </thead>
          <tbody>
            {tickers.map((r) => (
              <tr key={`${r.exchange}:${r.symbol}`}>
                <td>{r.symbol}</td>
                <td>{r.exchange}</td>
                <td>{r.ticker.price.toFixed(4)}</td>
                <td>{r.ticker.bid.toFixed(4)}</td>
                <td>{r.ticker.ask.toFixed(4)}</td>
                <td>{(r.ticker.spread_pct * 100).toFixed(3)}%</td>
                <td>{r.ticker.ts}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
