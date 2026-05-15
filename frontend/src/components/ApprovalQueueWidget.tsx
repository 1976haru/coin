/** ApprovalQueueWidget — 체크리스트 #74. 대시보드용 요약 위젯. */
import { useEffect, useState } from "react";
import { fetchApprovals, type ApprovalItem } from "../api/approvals";
import { useAdminToken } from "../contexts/AdminTokenContext";

export default function ApprovalQueueWidget() {
  const { token, hasToken } = useAdminToken();
  const [items, setItems] = useState<ApprovalItem[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!hasToken) return;
    let cancelled = false;
    const load = async () => {
      try {
        const r = await fetchApprovals(token);
        if (!cancelled) {
          setItems(r.items.filter((x) => x.status === "PENDING"));
          setError(null);
        }
      } catch (e) {
        if (!cancelled) setError(String(e));
      }
    };
    load();
    const id = setInterval(load, 5000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [token, hasToken]);

  if (!hasToken) {
    return (
      <section className="card">
        <h2>승인 대기열</h2>
        <p className="muted small">⚠ admin 토큰 필요</p>
      </section>
    );
  }

  return (
    <section className="card">
      <h2>승인 대기열 ({items.length})</h2>
      {error && <p className="error-text">{error}</p>}
      {items.length === 0 ? (
        <p className="muted small">대기 중인 승인 없음</p>
      ) : (
        <ul className="approval-summary">
          {items.slice(0, 5).map((it) => (
            <li key={it.id}>
              <span className={`source-tag source-${it.source}`}>{it.source}</span>
              <span>{(it.order.symbol as string) ?? ""}</span>
              <span className="muted small">{it.reason}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
