/** 체크리스트 #7: 범용 상태 카드.
 *
 * Dashboard / Risk / Agent 등에서 단일 상태값(라벨+값+한줄 설명)을 보여줄 때 사용.
 * tone 으로 시각 강조 (정상/주의/위험/info). 본 단계에서는 실시간 데이터를 fetch
 * 하지 않으며, 부모가 placeholder 값을 그대로 내려보낸다.
 *
 * 시세 freshness 전용 위젯은 `FreshnessCard.tsx` 로 분리되었다.
 */
import type { ReactNode } from "react";

export type StatusTone = "ok" | "warn" | "danger" | "info";

export interface StatusCardProps {
  title: string;
  value: ReactNode;
  description?: ReactNode;
  tone?: StatusTone;
}

const TONE_CLASS: Record<StatusTone, string> = {
  ok:     "card-ok",
  warn:   "card-warn",
  danger: "card-danger",
  info:   "",
};

export default function StatusCard({
  title,
  value,
  description,
  tone = "info",
}: StatusCardProps) {
  const toneClass = TONE_CLASS[tone];
  return (
    <section className={toneClass ? `card status-card ${toneClass}` : "card status-card"}>
      <h2>{title}</h2>
      <div className="status-card-value">{value}</div>
      {description && <p className="status-card-desc">{description}</p>}
    </section>
  );
}
