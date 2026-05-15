/** ApprovalsPage — 체크리스트 #74 Approval UI. 승인 대기열 + 승인/거부 액션. */
import { useEffect, useState } from "react";
import {
  fetchApprovals, approveItem, rejectItem,
  type ApprovalItem,
} from "../api/approvals";
import { useAdminToken } from "../contexts/AdminTokenContext";

export default function ApprovalsPage() {
  const { token, hasToken } = useAdminToken();
  const [items, setItems] = useState<ApprovalItem[]>([]);
  const [filter, setFilter] = useState<"all" | "ai" | "manual">("all");
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  async function load() {
    if (!hasToken) return;
    try {
      const r = await fetchApprovals(token);
      setItems(r.items);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }

  useEffect(() => {
    void load();
    if (!hasToken) return;
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, hasToken]);

  async function handleAction(id: string, approve: boolean) {
    setBusyId(id);
    try {
      if (approve) await approveItem(id, token);
      else await rejectItem(id, token);
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusyId(null);
    }
  }

  if (!hasToken) {
    return (
      <section className="card">
        <h2>승인 대기열</h2>
        <p className="muted">⚠ admin 토큰이 필요합니다 (헤더 우측 admin 로그인)</p>
      </section>
    );
  }

  const visible = items.filter(
    (it) =>
      it.status === "PENDING" &&
      (filter === "all" || it.source === filter)
  );

  return (
    <div className="grid">
      <section className="card">
        <h2>승인 대기열 ({visible.length})</h2>
        <div className="row">
          <button
            className={filter === "all" ? "filter-btn active" : "filter-btn"}
            onClick={() => setFilter("all")}
          >
            전체
          </button>
          <button
            className={filter === "ai" ? "filter-btn active" : "filter-btn"}
            onClick={() => setFilter("ai")}
          >
            AI 제안
          </button>
          <button
            className={filter === "manual" ? "filter-btn active" : "filter-btn"}
            onClick={() => setFilter("manual")}
          >
            수동
          </button>
        </div>
        {error && <p className="error-text">{error}</p>}
        {visible.length === 0 && (
          <p className="muted">대기 중인 항목이 없습니다.</p>
        )}
        {visible.map((it) => (
          <article key={it.id} className="approval-card">
            <header className="approval-card-head">
              <span className={`source-tag source-${it.source}`}>{it.source}</span>
              <strong>{(it.order.symbol as string) ?? ""}</strong>
              <span className="muted small">{(it.order.side as string) ?? ""}</span>
              <span className="muted small">
                notional={(it.order.notional_usdt as number) ?? "-"}
              </span>
            </header>
            <p>{it.reason}</p>
            {it.agent_explain && (
              <pre className="agent-explain">{it.agent_explain}</pre>
            )}
            <footer className="approval-card-actions">
              <button
                className="btn-approve"
                onClick={() => void handleAction(it.id, true)}
                disabled={busyId === it.id}
              >
                {busyId === it.id ? "처리중…" : "승인"}
              </button>
              <button
                className="btn-reject"
                onClick={() => void handleAction(it.id, false)}
                disabled={busyId === it.id}
              >
                거부
              </button>
              <span className="muted small">expires: {it.expires_at}</span>
            </footer>
          </article>
        ))}
      </section>
    </div>
  );
}
