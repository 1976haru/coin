/** 체크리스트 #7: 전략 목록 placeholder.
 *
 * 본 단계는 표시 전용. 실제 신호 계산은 backend strategies/ 에서만 수행되며,
 * frontend 는 활성/비활성 상태와 메타만 노출한다.
 */

interface StrategyRow {
  name: string;
  description: string;
  enabled: boolean;
}

const STRATEGIES: StrategyRow[] = [
  { name: "Momentum Strategy",       description: "추세 추종 (placeholder)",        enabled: true  },
  { name: "Mean Reversion Strategy", description: "평균 회귀 (placeholder)",        enabled: false },
  { name: "Breakout Strategy",       description: "변동성 돌파 (placeholder)",      enabled: false },
  { name: "Volume Spike Strategy",   description: "거래량 급증 감지 (placeholder)", enabled: false },
];

export default function StrategyPage() {
  return (
    <div className="page-stack">
      <h2 className="page-title">Strategy</h2>
      <p className="muted">
        전략 목록 (placeholder). 본 단계에서는 활성/비활성 표시만 합니다 —
        실제 신호 계산은 구현하지 않았습니다.
      </p>
      <table className="data-table">
        <thead>
          <tr>
            <th>전략명</th>
            <th>설명</th>
            <th>상태</th>
          </tr>
        </thead>
        <tbody>
          {STRATEGIES.map((s) => (
            <tr key={s.name}>
              <td>{s.name}</td>
              <td className="muted">{s.description}</td>
              <td>
                <span
                  className={s.enabled ? "tag-enabled" : "tag-disabled"}
                  aria-label={s.enabled ? "enabled" : "disabled"}
                >
                  {s.enabled ? "enabled" : "disabled"}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
