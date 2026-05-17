/** 체크리스트 #7: PC 사이드바 네비게이션.
 *
 * 7개 메뉴(Dashboard/Agent/Strategy/Approval/Risk/Logs/Settings) 노출.
 * NavLink 의 isActive 로 현재 페이지를 시각 구분한다.
 */
import { NavLink } from "react-router-dom";
import { NAV_ITEMS } from "./navItems";

export default function Sidebar() {
  return (
    <aside className="app-sidebar" aria-label="Primary navigation">
      <nav>
        <ul className="sidebar-list">
          {NAV_ITEMS.map((item) => (
            <li key={item.to}>
              <NavLink
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  isActive ? "sidebar-link active" : "sidebar-link"
                }
              >
                {item.label}
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>
    </aside>
  );
}
