/** 체크리스트 #7: 로그 화면 placeholder.
 *
 * 시스템 / Agent / 주문 / 승인 로그 4개 섹션. 본 단계는 sample 2~3건만 표시.
 * 실제 감사로그 수집은 backend audit/ 와 향후 연결한다.
 */
import { useState } from "react";

type LogTab = "system" | "agent" | "order" | "approval";

interface SampleLog {
  ts: string;
  message: string;
}

const SAMPLES: Record<LogTab, SampleLog[]> = {
  system: [
    { ts: "12:00:01", message: "frontend skeleton 부팅 — paper mode" },
    { ts: "12:00:02", message: "/api/health → ok" },
  ],
  agent: [
    { ts: "12:00:05", message: "AgentOrchestrator 대기(standby) — 신호 없음" },
  ],
  order: [
    { ts: "12:00:10", message: "주문 시도 없음 (paper mode)" },
  ],
  approval: [
    { ts: "12:00:15", message: "pending approval: 0" },
  ],
};

const TABS: { key: LogTab; label: string }[] = [
  { key: "system",   label: "시스템 로그" },
  { key: "agent",    label: "Agent 판단" },
  { key: "order",    label: "주문 로그" },
  { key: "approval", label: "승인 로그" },
];

export default function LogsPage() {
  const [tab, setTab] = useState<LogTab>("system");
  return (
    <div className="page-stack">
      <h2 className="page-title">Logs</h2>
      <p className="muted">
        placeholder — 실제 로그 수집 API 는 아직 연결되지 않았습니다.
        sample 로그만 표시됩니다.
      </p>

      <div className="tab-row" role="tablist">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={tab === t.key}
            className={tab === t.key ? "filter-btn active" : "filter-btn"}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>

      <ul className="audit-list">
        {SAMPLES[tab].map((log, i) => (
          <li key={i}>
            <span className="ts">{log.ts}</span>
            <span>{log.message}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
