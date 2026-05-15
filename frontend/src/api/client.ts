/**
 * 공통 fetch 래퍼 — 체크리스트 #7.
 *
 * Vite dev 환경: 같은 origin으로 요청 → vite.config.ts 의 proxy 가 백엔드(8000)로 전달.
 * Production: FastAPI 가 dist/ 를 /static 마운트하고 같은 origin에서 /api/* 응답.
 *
 * secret 은 frontend 에 절대 두지 않는다 (CLAUDE.md §2.1.5). admin 토큰은
 * 호출 시점에 사용자가 입력한 값을 헤더로 첨부한다.
 */

export class ApiError extends Error {
  constructor(public status: number, public body: unknown, message?: string) {
    super(message ?? `API error ${status}`);
  }
}

export interface RequestOptions extends RequestInit {
  adminToken?: string;
}

export async function apiFetch<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const headers = new Headers(opts.headers);
  if (!headers.has("Content-Type") && opts.body) {
    headers.set("Content-Type", "application/json");
  }
  if (opts.adminToken) {
    headers.set("X-Admin-Token", opts.adminToken);
  }

  const res = await fetch(path, { ...opts, headers });
  const text = await res.text();
  let body: unknown = text;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      // 텍스트 응답 그대로 둠
    }
  }
  if (!res.ok) {
    throw new ApiError(res.status, body, `${opts.method ?? "GET"} ${path} → ${res.status}`);
  }
  return body as T;
}
