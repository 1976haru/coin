/** /api/audit 클라이언트 (admin 토큰 필요) — 체크리스트 #7. */
import { apiFetch } from "./client";

export interface AuditEvent {
  ts: string;
  event_type: string;
  payload: Record<string, unknown>;
}

export interface AuditTailResponse {
  events: AuditEvent[];
  total: number;
}

export function fetchAuditTail(adminToken: string, limit = 100) {
  return apiFetch<AuditTailResponse>(`/api/audit?limit=${limit}`, { adminToken });
}
