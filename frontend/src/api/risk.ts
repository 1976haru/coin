/** /api/kill-switch / /api/promotion/* — 체크리스트 #75. */
import { apiFetch } from "./client";

export interface KillSwitchRequest {
  active: boolean;
  reason?: string;
}

export interface KillSwitchResponse {
  active: boolean;
  reason: string;
}

export function setKillSwitch(req: KillSwitchRequest, adminToken: string) {
  return apiFetch<KillSwitchResponse>("/api/kill-switch", {
    method: "POST",
    body: JSON.stringify(req),
    adminToken,
  });
}

export interface PromotionGateMetrics {
  passed: boolean;
  from_mode: string;
  to_mode: string;
  reason: string;
}

export function fetchPaperGate() {
  return apiFetch<PromotionGateMetrics>("/api/promotion/paper-gate");
}

export function fetchShadowGate() {
  return apiFetch<PromotionGateMetrics>("/api/promotion/shadow-gate");
}
