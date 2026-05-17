/** 체크리스트 #7: AI Agent 관제 placeholder.
 *
 * 추후 들어갈 섹션:
 *   1. 현재 상태 / 활성 전략 조합
 *   2. 시장상황 판단 근거
 *   3. 진입/청산 추천 시나리오 + 설명 로그
 *
 * 본 단계는 placeholder. Agent 가 직접 주문을 만들지 않는다는 안전 원칙
 * (CLAUDE.md §2.3) 을 안내 문구로 노출한다.
 */
import StatusCard from "../components/StatusCard";

export default function AgentPage() {
  return (
    <div className="page-stack">
      <h2 className="page-title">Agent</h2>

      <div className="dashboard-grid">
        <StatusCard
          title="Agent Status"
          value="standby"
          description="AI Agent 대기 중 — 분석/추천만, 직접 주문 금지"
          tone="info"
        />
      </div>

      <section className="section">
        <h3 className="section-title">전략 조합</h3>
        <p className="muted">활성 전략 조합과 가중치를 표시할 영역입니다.</p>
      </section>

      <section className="section">
        <h3 className="section-title">시장상황 판단</h3>
        <p className="muted">
          현재 시장 레짐(추세/횡보/변동성)과 판단 근거가 표시될 영역입니다.
        </p>
      </section>

      <section className="section">
        <h3 className="section-title">진입 / 청산 설명 로그</h3>
        <p className="muted">
          Agent 가 생성한 매매 시나리오와 설명(reason)이 표시될 영역입니다.
          실제 주문은 single-path(OrderGateway) 를 통해서만 실행됩니다.
        </p>
      </section>
    </div>
  );
}
