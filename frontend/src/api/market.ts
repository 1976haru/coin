/** /api/market/* 클라이언트 — 체크리스트 #15 Market Data Collector.
 *
 * 읽기 전용 클라이언트. 수동 수집 트리거(POST)는 admin token 이 명시될 때만
 * 사용하며, 본 페이지는 기본적으로 조회만 제공한다.
 */
import { apiFetch } from "./client";

export interface MarketTicker {
  symbol: string;
  price: number;
  bid: number;
  ask: number;
  spread_pct: number;
  volume_24h: number;
  ts: string;
}

export interface TickerRow {
  symbol: string;
  exchange: string;
  ticker: MarketTicker;
}

export interface TickersResponse {
  tickers: TickerRow[];
  exchanges: string[];
}

export interface CollectorStatus {
  last_collected_at: string | null;
  last_symbol_count: number;
  last_success_count: number;
  last_failure_count: number;
  last_includes: string[];
  last_list_name: string | null;
  sources: string[];
  fx_source: string | null;
  freshness_threshold_sec: number;
  cache_size: number;
  mode: string;
}

export function fetchTickers(filter: {
  list_name?: string;
  exchange?: string;
  enabled_only?: boolean;
} = {}) {
  const q = new URLSearchParams();
  if (filter.list_name) q.set("list_name", filter.list_name);
  if (filter.exchange) q.set("exchange", filter.exchange);
  if (filter.enabled_only) q.set("enabled_only", "true");
  const qs = q.toString();
  return apiFetch<TickersResponse>(`/api/market/tickers${qs ? `?${qs}` : ""}`);
}

export function fetchCollectorStatus() {
  return apiFetch<CollectorStatus>("/api/market/collector/status");
}
