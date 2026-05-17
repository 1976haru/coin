/** 체크리스트 #7: 모바일 하단 네비게이션.
 *
 * 핵심 메뉴(Dashboard/Agent/Risk/Logs/Settings)만 노출 — 작은 화면 가독성 우선.
 * 데스크탑에서는 CSS 로 숨긴다 (전체 메뉴는 Sidebar 가 담당).
 */
import { NavLink } from "react-router-dom";
import { NAV_ITEMS } from "./navItems";

export default function MobileNav() {
  const items = NAV_ITEMS.filter((i) => i.showOnMobile);
  return (
    <nav className="mobile-nav" aria-label="Mobile navigation">
      <ul className="mobile-nav-list">
        {items.map((item) => (
          <li key={item.to}>
            <NavLink
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                isActive ? "mobile-nav-link active" : "mobile-nav-link"
              }
            >
              {item.shortLabel ?? item.label}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}
