/** /api/status, /api/freshness 타입 정의 — 체크리스트 #7. */
import { apiFetch } from "./client";

export interface AppStatus {
  app: string;
  version: string;
  trading_mode: string;
  flags: Record<string, boolean>;
}

export function fetchStatus() {
  return apiFetch<AppStatus>("/api/status");
}

export interface FreshnessStatus {
  ok: boolean;
  reason: string;
  source?: string;
  age_sec?: number;
}

export function fetchFreshness() {
  return apiFetch<FreshnessStatus>("/api/freshness");
}
