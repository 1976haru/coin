/** /api/approval/* 클라이언트 — 체크리스트 #74. */
import { apiFetch } from "./client";

export interface ApprovalItem {
  id: string;
  order: Record<string, unknown>;
  reason: string;
  created_at: string;
  expires_at: string;
  status: "PENDING" | "APPROVED" | "REJECTED" | "EXPIRED";
  source: string;          // system / strategy / ai / manual (#58)
  agent_explain: string;
}

export interface ApprovalQueueResponse {
  items: ApprovalItem[];
  count_pending: number;
}

export function fetchApprovals(adminToken: string) {
  return apiFetch<ApprovalQueueResponse>("/api/approval/queue", { adminToken });
}

export function approveItem(itemId: string, adminToken: string) {
  return apiFetch<ApprovalItem>(`/api/approval/${itemId}`, {
    method: "POST",
    body: JSON.stringify({ approved: true }),
    adminToken,
  });
}

export function rejectItem(itemId: string, adminToken: string) {
  return apiFetch<ApprovalItem>(`/api/approval/${itemId}`, {
    method: "POST",
    body: JSON.stringify({ approved: false }),
    adminToken,
  });
}
