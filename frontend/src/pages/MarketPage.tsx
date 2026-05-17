/** Market 페이지 — 체크리스트 #15 + #16. 읽기 전용.
 *
 * 표시 항목:
 *   - 수집기 상태 (sources / 마지막 수집 시각 / 성공·실패 / 모드)
 *   - Data Freshness 패널 (fresh/stale/missing/reconnecting + stale 목록)
 *   - 현재 캐시된 ticker 목록
 *
 * 수동 collect / reconnecting 토글 버튼은 만들지 않는다 — 본 페이지는 read-only.
 * 운영자가 CLI 또는 admin token 으로 POST endpoint 를 호출해 변경한다.
 */
import { useEffect, useState } from "react";
import {
  fetchTickers,
  fetchCollectorStatus,
  fetchFreshness,
  type TickerRow,
  type CollectorStatus,
  type FreshnessResponse,
} from "../api/market";

export default function MarketPage() {
  const [tickers, setTickers] = useState<TickerRow[]>([]);
  const [status, setStatus] = useState<CollectorStatus | null>(null);
  const [fresh, setFresh] = useState<FreshnessResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([fetchTickers(), fetchCollectorStatus(), fetchFreshness()])
      .then(([t, s, f]) => {
        setTickers(t.tickers);
        setStatus(s);
        setFresh(f);
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
        <>
          <h3>Collector Status</h3>
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
        </>
      )}

      {fresh && (
        <>
          <h3>
            Data Freshness{" "}
            <small className="muted">
              ({fresh.ok ? "OK" : "BLOCKS NEW ENTRIES"})
            </small>
          </h3>
          <p className="muted" style={{ marginBottom: "0.5rem" }}>
            stale 데이터 또는 reconnecting 상태에서는 신규 BUY/OPEN 진입이 차단됩니다.
            SELL/EXIT/CLOSE 등 위험 축소 동작은 막지 않습니다.
          </p>
          <table className="data-table" style={{ marginBottom: "1rem" }}>
            <tbody>
              <tr><td>fresh</td><td>{fresh.summary.counts.fresh}</td></tr>
              <tr><td>stale</td><td>{fresh.summary.counts.stale}</td></tr>
              <tr><td>missing</td><td>{fresh.summary.counts.missing}</td></tr>
              <tr><td>total</td><td>{fresh.summary.counts.total}</td></tr>
              <tr>
                <td>reconnecting_scopes</td>
                <td>{fresh.summary.counts.reconnecting_scopes}</td>
              </tr>
              <tr>
                <td>blocks_new_entries</td>
                <td>{fresh.summary.blocks_new_entries ? "true" : "false"}</td>
              </tr>
              <tr><td>now</td><td>{fresh.summary.now}</td></tr>
            </tbody>
          </table>

          {fresh.summary.reconnecting.length > 0 && (
            <>
              <h4>Reconnecting</h4>
              <table className="data-table" style={{ marginBottom: "1rem" }}>
                <thead>
                  <tr>
                    <th>symbol</th><th>exchange</th><th>data_type</th><th>reason</th>
                  </tr>
                </thead>
                <tbody>
                  {fresh.summary.reconnecting.map((r, i) => (
                    <tr key={i}>
                      <td>{r.symbol ?? "*"}</td>
                      <td>{r.exchange ?? "*"}</td>
                      <td>{r.data_type ?? "*"}</td>
                      <td>{r.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}

          {fresh.summary.records.some((r) => r.stale) && (
            <>
              <h4>Stale Records</h4>
              <table className="data-table" style={{ marginBottom: "1rem" }}>
                <thead>
                  <tr>
                    <th>symbol</th><th>exchange</th><th>data_type</th>
                    <th>timeframe</th><th>age_s</th><th>max_age_s</th>
                  </tr>
                </thead>
                <tbody>
                  {fresh.summary.records.filter((r) => r.stale).map((r, i) => (
                    <tr key={i}>
                      <td>{r.symbol}</td>
                      <td>{r.exchange}</td>
                      <td>{r.data_type}</td>
                      <td>{r.timeframe ?? "—"}</td>
                      <td>{r.age_seconds?.toFixed(1) ?? "—"}</td>
                      <td>{r.max_age_seconds.toFixed(1)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </>
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
