/**
 * 체크리스트 #7 Frontend Skeleton — 백엔드 API base URL + health 헬퍼.
 *
 * 기존 `src/api/client.ts` 가 vite proxy 기반 fetch 래퍼를 이미 제공하지만,
 * 스펙(체크리스트 #7)이 `src/lib/api.ts` 위치와 `VITE_API_BASE_URL` 환경변수
 * 우선순위를 요구하므로 본 파일을 별도로 둔다. 실주문 관련 함수는 두지 않는다.
 */
import type { TradingMode } from "./types";

/**
 * 백엔드 base URL.
 *
 * 우선순위:
 *   1. `import.meta.env.VITE_API_BASE_URL` (빌드 시 주입)
 *   2. fallback: `http://localhost:8000`
 *
 * 같은 origin 으로 운용할 때는 빈 문자열로 두는 것을 권장하지만,
 * 본 단계는 별도 dev 서버를 가정한다.
 */
export const API_BASE_URL: string =
  (import.meta.env?.VITE_API_BASE_URL as string | undefined) ??
  "http://localhost:8000";

export interface HealthResponse {
  status: "ok" | string;
  service: string;
  mode: TradingMode | string;
}

/**
 * 백엔드 `/api/health` 호출.
 *
 * 본 단계는 placeholder. 실패 시 호출자가 처리하도록 그대로 예외를 던진다.
 * secret 헤더는 붙이지 않는다 (frontend 에 secret 금지 — CLAUDE.md §2.1.5).
 */
export async function fetchHealth(signal?: AbortSignal): Promise<HealthResponse> {
  const res = await fetch(`${API_BASE_URL}/api/health`, { signal });
  if (!res.ok) {
    throw new Error(`/api/health → ${res.status}`);
  }
  return (await res.json()) as HealthResponse;
}
