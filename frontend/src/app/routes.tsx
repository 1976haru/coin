/** 체크리스트 #7: 라우트 정의.
 *
 * 스펙 7개 경로:
 *   / → DashboardPage
 *   /agent → AgentPage
 *   /strategy → StrategyPage
 *   /approval → ApprovalPage   (단수)
 *   /risk → RiskPage
 *   /logs → LogsPage
 *   /settings → SettingsPage
 *
 * 기존 경로(/approvals, /watchlist, /audit) 는 #73 ~ #76 위젯이 의존하므로
 * 유지한다 (회귀 방지).
 */
import { Route, Routes } from "react-router-dom";

import AppLayout from "../layouts/AppLayout";
import DashboardPage from "../pages/DashboardPage";
import AgentPage from "../pages/AgentPage";
import StrategyPage from "../pages/StrategyPage";
import ApprovalPage from "../pages/ApprovalPage";
import ApprovalsPage from "../pages/ApprovalsPage";
import RiskPage from "../pages/RiskPage";
import LogsPage from "../pages/LogsPage";
import SettingsPage from "../pages/SettingsPage";
import WatchlistPage from "../pages/WatchlistPage";
import AuditPage from "../pages/AuditPage";
import MarketPage from "../pages/MarketPage";

export default function AppRoutes() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/agent" element={<AgentPage />} />
        <Route path="/strategy" element={<StrategyPage />} />
        <Route path="/approval" element={<ApprovalPage />} />
        <Route path="/risk" element={<RiskPage />} />
        <Route path="/logs" element={<LogsPage />} />
        <Route path="/settings" element={<SettingsPage />} />

        {/* 기존 라우트 — Phase 9 위젯(#73-#76) 호환 유지 */}
        <Route path="/approvals" element={<ApprovalsPage />} />
        <Route path="/watchlist" element={<WatchlistPage />} />
        <Route path="/audit" element={<AuditPage />} />
        {/* #15 Market Data — 사이드바 #7 7개 메뉴 사양은 유지하고 라우트만 노출 */}
        <Route path="/market" element={<MarketPage />} />
      </Route>
    </Routes>
  );
}
