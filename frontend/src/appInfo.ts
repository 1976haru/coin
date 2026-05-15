/**
 * 앱 메타 정보 (frontend) — 체크리스트 #5 Agent Trader Naming.
 *
 * Vite/React 마이그레이션 (#7) 후 본 모듈이 모든 컴포넌트의 헤더/도움말/모달의
 * 단일 진리 소스가 된다. 현재는 단일 HTML 데모(index.html)가 사용 중이며,
 * 본 파일은 #7 작업 시 즉시 활용할 수 있도록 placeholder 로 작성한다.
 *
 * 원칙:
 * - 값은 backend `/api/app` + `/api/release-notes` 로부터 fetch.
 * - frontend 빌드에 secret 박지 않는다 (브랜드 정보만).
 * - GitHub Pages demo 모드에서는 mockData 의 값을 사용 (#79).
 */

export interface AppInfo {
  name: string;
  version: string;
  tagline: string;
  repo: string;
}

export interface ReleaseNote {
  version: string;
  date: string;          // YYYY-MM-DD
  title: string;
  highlights: string[];
}

// 빌드 타임 fallback (백엔드 응답 실패 시 또는 demo 모드)
export const FALLBACK_APP_INFO: AppInfo = {
  name:    "Agent Trader Crypto OS",
  version: "1.0.0-alpha",
  tagline: "AI Agent 기반 코인 자동매매 연구·검증·관제 플랫폼",
  repo:    "https://github.com/1976haru/coin",
};

export async function fetchAppInfo(baseUrl = ""): Promise<AppInfo> {
  try {
    const r = await fetch(`${baseUrl}/api/app`);
    if (!r.ok) return FALLBACK_APP_INFO;
    return await r.json();
  } catch {
    return FALLBACK_APP_INFO;
  }
}

export async function fetchReleaseNotes(baseUrl = ""): Promise<ReleaseNote[]> {
  try {
    const r = await fetch(`${baseUrl}/api/release-notes`);
    if (!r.ok) return [];
    const d = await r.json();
    return (d.items ?? []) as ReleaseNote[];
  } catch {
    return [];
  }
}
