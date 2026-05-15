/** /api/watchlist 클라이언트 — 체크리스트 #7 + #14. */
import { apiFetch } from "./client";

export interface WatchlistEntry {
  id: number;
  list_name: string;
  symbol: string;
  exchange: string;
  enabled: boolean;
  max_notional_usdt_override: number | null;
  tags: string[];
  note: string;
  created_at: string | null;
  updated_at: string | null;
}

export interface WatchlistResponse {
  entries: WatchlistEntry[];
  lists: string[];
}

export interface WatchlistFilter {
  list_name?: string;
  exchange?: string;
  enabled_only?: boolean;
}

export function fetchWatchlist(filter: WatchlistFilter = {}) {
  const q = new URLSearchParams();
  if (filter.list_name) q.set("list_name", filter.list_name);
  if (filter.exchange) q.set("exchange", filter.exchange);
  if (filter.enabled_only) q.set("enabled_only", "true");
  const qs = q.toString();
  return apiFetch<WatchlistResponse>(`/api/watchlist${qs ? `?${qs}` : ""}`);
}

export interface AddWatchlistRequest {
  symbol: string;
  exchange?: string;
  list_name?: string;
  enabled?: boolean;
  max_notional_usdt_override?: number | null;
  tags?: string[];
  note?: string;
}

export function addWatchlist(req: AddWatchlistRequest, adminToken: string) {
  return apiFetch<WatchlistEntry>("/api/watchlist", {
    method: "POST",
    body: JSON.stringify(req),
    adminToken,
  });
}

export function removeWatchlist(id: number, adminToken: string) {
  return apiFetch<void>(`/api/watchlist/${id}`, {
    method: "DELETE",
    adminToken,
  });
}

export function setWatchlistEnabled(id: number, enabled: boolean, adminToken: string) {
  return apiFetch<WatchlistEntry>(`/api/watchlist/${id}/${enabled ? "enable" : "disable"}`, {
    method: "PATCH",
    adminToken,
  });
}
