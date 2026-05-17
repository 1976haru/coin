/** 체크리스트 #7: 대시보드 (관제 홈).
 *
 * "5초 안에 현재 상태를 파악할 수 있는 구조" — 핵심 6개 카드 + 안전 안내.
 * 본 단계는 placeholder 값. 추후 `/api/status` 응답으로 채울 자리이다.
 *
 * 기존 위젯(FreshnessCard / KillSwitchButton / ApprovalQueueWidget)은 하단
 * "운영 위젯" 섹션에서 그대로 유지하여 #73 ~ #76 기능 회귀가 없게 한다.
 */
import StatusCard from "../components/StatusCard";
import ModeBadge from "../components/ModeBadge";
import FreshnessCard from "../components/FreshnessCard";
import KillSwitchButton from "../components/KillSwitchButton";
import ApprovalQueueWidget from "../components/ApprovalQueueWidget";

export default function DashboardPage() {
  return (
    <div className="page-stack">
      <h2 className="page-title">Dashboard</h2>

      <div className="dashboard-grid">
        <StatusCard
          title="Trading Mode"
          value={<ModeBadge mode="paper" />}
          description="paper — 실거래 주문 비활성"
          tone="ok"
        />
        <StatusCard
          title="Agent Status"
          value="standby"
          description="AI Agent 대기 중"
          tone="info"
        />
        <StatusCard
          title="Risk Status"
          value="normal"
          description="모든 한도 정상"
          tone="ok"
        />
        <StatusCard
          title="Virtual PnL"
          value="0"
          description="가상 잔고 손익 (paper)"
          tone="info"
        />
        <StatusCard
          title="Open Positions"
          value="0"
          description="현재 열린 가상 포지션"
          tone="info"
        />
        <StatusCard
          title="Approval Pending"
          value="0"
          description="승인 대기 중 항목"
          tone="info"
        />
      </div>

      <p className="callout-info">
        현재 실거래 주문은 비활성화되어 있습니다. 모든 신호와 주문은 paper
        mode 에서만 처리됩니다.
      </p>

      <section className="section">
        <h3 className="section-title">운영 위젯</h3>
        <div className="dashboard-grid">
          <FreshnessCard />
          <KillSwitchButton />
          <ApprovalQueueWidget />
        </div>
      </section>
    </div>
  );
}
