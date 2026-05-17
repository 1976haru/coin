/**
 * 체크리스트 #7 Frontend Skeleton — 공통 타입 정의.
 *
 * 본 파일은 UI 컴포넌트가 사용할 최소 타입만 둔다. 백엔드 wire 타입은
 * `src/api/health.ts` 등 도메인 모듈에 따로 둔다.
 */

/**
 * 거래 모드 (UI 표시용 lowercase).
 *
 * 백엔드 `TradingMode` enum 은 6단계(SIMULATION/PAPER/LIVE_SHADOW/
 * LIVE_MANUAL_APPROVAL/LIVE_AI_ASSIST/LIVE_AI_EXECUTION)이지만,
 * 본 #7 단계 UI 는 paper/mock/live 3가지로 단순화한다.
 * live 는 시각 구분만 두고 실거래 전환 기능은 구현하지 않는다.
 */
export type TradingMode = "paper" | "mock" | "live";

/** Agent 상태 표시 라벨. */
export type AgentStatus = "standby" | "running" | "paused" | "error";

/** Risk 게이트 상태 — Dashboard / RiskPage 의 카드 톤에 사용. */
export type RiskStatus = "normal" | "warning" | "blocked";
