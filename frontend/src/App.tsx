/**
 * App 셸 — 체크리스트 #7 + #73·#74·#75·#80 Phase 9.
 */
import { NavLink, Route, Routes } from "react-router-dom";
import Header from "./components/Header";
import AdminTokenInput from "./components/AdminTokenInput";
import VersionWatcher from "./components/VersionWatcher";
import DashboardPage from "./pages/DashboardPage";
import WatchlistPage from "./pages/WatchlistPage";
import AuditPage from "./pages/AuditPage";
import ApprovalsPage from "./pages/ApprovalsPage";
import RiskPage from "./pages/RiskPage";
import { AdminTokenProvider } from "./contexts/AdminTokenContext";

export default function App() {
  return (
    <AdminTokenProvider>
      <div className="app-shell">
        <VersionWatcher />
        <div className="header-row">
          <Header />
          <AdminTokenInput />
        </div>
        <nav className="primary-nav">
          <NavLink to="/" end className={navClass}>대시보드</NavLink>
          <NavLink to="/approvals" className={navClass}>승인 대기열</NavLink>
          <NavLink to="/risk" className={navClass}>Risk</NavLink>
          <NavLink to="/watchlist" className={navClass}>Watchlist</NavLink>
          <NavLink to="/audit" className={navClass}>Audit</NavLink>
        </nav>
        <main className="page">
          <Routes>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/approvals" element={<ApprovalsPage />} />
            <Route path="/risk" element={<RiskPage />} />
            <Route path="/watchlist" element={<WatchlistPage />} />
            <Route path="/audit" element={<AuditPage />} />
          </Routes>
        </main>
      </div>
    </AdminTokenProvider>
  );
}

function navClass({ isActive }: { isActive: boolean }) {
  return isActive ? "nav-link active" : "nav-link";
}
