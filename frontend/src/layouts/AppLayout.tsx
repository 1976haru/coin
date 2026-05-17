/** 체크리스트 #7: 앱 레이아웃 셸.
 *
 * 구조:
 *   - 상단 고정: EmergencyStopBar (관제 화면이므로 항상 노출)
 *   - PC: Sidebar (좌) + main (우)
 *   - 모바일: MobileNav (하단 고정) + main (단일 컬럼)
 *
 * 본 레이아웃은 라우팅 매칭된 자식 페이지를 `<Outlet />` 로 렌더링한다.
 * 기존 Header / AdminTokenInput / VersionWatcher 는 본 셸 안쪽 main 상단에
 * 그대로 둔다 (체크리스트 #5/#73-#76 위젯 유지).
 */
import { Outlet } from "react-router-dom";
import Sidebar from "../components/navigation/Sidebar";
import MobileNav from "../components/navigation/MobileNav";
import EmergencyStopBar from "../components/EmergencyStopBar";
import Header from "../components/Header";
import AdminTokenInput from "../components/AdminTokenInput";
import VersionWatcher from "../components/VersionWatcher";

export default function AppLayout() {
  return (
    <div className="app-layout">
      <EmergencyStopBar />
      <div className="app-layout-body">
        <Sidebar />
        <main className="app-layout-main">
          <VersionWatcher />
          <div className="header-row">
            <Header />
            <AdminTokenInput />
          </div>
          <div className="page-content">
            <Outlet />
          </div>
        </main>
      </div>
      <MobileNav />
    </div>
  );
}
