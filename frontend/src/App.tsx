/**
 * App 셸 — 체크리스트 #7 Frontend Skeleton.
 *
 * 본 컴포넌트는 라우팅과 전역 Provider 만 담당한다. 레이아웃(사이드바/모바일
 * 네비/EmergencyStopBar)과 페이지 렌더링은 `AppLayout` + `AppRoutes` 가 담당.
 *
 * 기존 Phase 9 위젯(#73-#76) Provider 는 그대로 유지된다.
 */
import AppRoutes from "./app/routes";
import { AdminTokenProvider } from "./contexts/AdminTokenContext";

export default function App() {
  return (
    <AdminTokenProvider>
      <AppRoutes />
    </AdminTokenProvider>
  );
}
