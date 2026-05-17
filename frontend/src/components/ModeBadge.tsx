/** 체크리스트 #7: Trading Mode 배지.
 *
 * paper/mock/live 를 색상으로 구분. live 는 "위험 모드" 시각 강조만 두고
 * 본 단계에서는 어떤 실거래 전환 기능도 트리거하지 않는다 (CLAUDE.md §2.2).
 */
import type { TradingMode } from "../lib/types";

export interface ModeBadgeProps {
  /** 표시할 모드. 기본 `paper`. */
  mode?: TradingMode;
}

const MODE_LABEL: Record<TradingMode, string> = {
  paper: "PAPER",
  mock:  "MOCK",
  live:  "LIVE",
};

const MODE_DESC: Record<TradingMode, string> = {
  paper: "가상 주문 — 실거래 없음",
  mock:  "모의 데이터 — 실거래 없음",
  live:  "⚠ 위험: 실거래 모드 (본 단계 비활성)",
};

export default function ModeBadge({ mode = "paper" }: ModeBadgeProps) {
  return (
    <span
      className={`mode-badge mode-badge-${mode}`}
      title={MODE_DESC[mode]}
      role="status"
      aria-label={`Trading Mode: ${MODE_LABEL[mode]}`}
    >
      {MODE_LABEL[mode]}
    </span>
  );
}
