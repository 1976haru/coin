/** 체크리스트 #7: 승인 요청 placeholder.
 *
 * 실거래 전환 / 고위험 주문 / 설정 변경 승인이 들어올 자리.
 * 본 단계는 placeholder — 현재 pending 0 으로 표시한다.
 * 기존 ApprovalsPage(복수) 는 #75 위젯과 연동된 실제 큐 UI로 유지된다.
 */
import StatusCard from "../components/StatusCard";

export default function ApprovalPage() {
  return (
    <div className="page-stack">
      <h2 className="page-title">Approval</h2>

      <div className="dashboard-grid">
        <StatusCard
          title="Pending Approvals"
          value="0"
          description="현재 승인 대기 중인 항목 없음"
          tone="ok"
        />
      </div>

      <p className="callout-warn">
        실거래 전환은 governance 승인 이후에만 가능합니다. 본 단계의 UI 는
        placeholder 이며, 실제 승인 흐름은 추후 백엔드 `/api/approvals` 와
        연결됩니다.
      </p>

      <section className="section">
        <h3 className="section-title">최근 승인 요청</h3>
        <p className="muted">아직 승인 요청이 없습니다.</p>
      </section>
    </div>
  );
}
