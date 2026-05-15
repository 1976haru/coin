/** 대시보드 — 체크리스트 #7 + #73. 위젯 통합. */
import StatusCard from "../components/StatusCard";
import KillSwitchButton from "../components/KillSwitchButton";
import ApprovalQueueWidget from "../components/ApprovalQueueWidget";

export default function DashboardPage() {
  return (
    <div className="grid">
      <StatusCard />
      <KillSwitchButton />
      <ApprovalQueueWidget />
    </div>
  );
}
