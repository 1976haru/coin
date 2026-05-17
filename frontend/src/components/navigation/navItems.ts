/** 체크리스트 #7: 사이드바/모바일 네비게이션 공유 메뉴 정의. */

export interface NavItem {
  /** 라우트 경로 — 단수형 (체크리스트 #7 스펙). */
  to: string;
  /** 메뉴 표시 라벨. */
  label: string;
  /** end=true 면 정확히 일치할 때만 active (`/` 용). */
  end?: boolean;
  /** 모바일 하단 네비에 노출 여부. */
  showOnMobile?: boolean;
  /** 모바일에서 사용할 짧은 라벨. */
  shortLabel?: string;
}

export const NAV_ITEMS: NavItem[] = [
  { to: "/",         label: "Dashboard", end: true, showOnMobile: true,  shortLabel: "Home" },
  { to: "/agent",    label: "Agent",                 showOnMobile: true },
  { to: "/strategy", label: "Strategy" },
  { to: "/approval", label: "Approval" },
  { to: "/risk",     label: "Risk",                  showOnMobile: true },
  { to: "/logs",     label: "Logs",                  showOnMobile: true },
  { to: "/settings", label: "Settings",              showOnMobile: true },
];
