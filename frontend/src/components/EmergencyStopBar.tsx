/** 체크리스트 #7: Emergency Stop 고정 바.
 *
 * **본 단계에서는 실제 중단 API 를 호출하지 않는다.**
 * 추후 execution/risk API 와 연결되도록 onClick 핸들러를 분리하고,
 * 본 단계에서는 alert 으로 "paper mode only" 안내만 띄운다.
 *
 * 실제 KillSwitch 동작은 `KillSwitchButton.tsx` 가 별도 admin token 인증과
 * 함께 처리한다. 본 컴포넌트는 사용자 가시성 + 모바일 즉시 접근을 위한
 * 항상-노출 영역이다.
 */
import { useCallback } from "react";

export interface EmergencyStopBarProps {
  /**
   * 추후 백엔드 연동 시 주입할 클릭 핸들러. 비주입 시 placeholder 동작.
   */
  onActivate?: () => void;
  /** 현재 활성 여부 (시각 표시용 — 본 단계는 항상 false 가정). */
  active?: boolean;
}

export default function EmergencyStopBar({
  onActivate,
  active = false,
}: EmergencyStopBarProps) {
  const handleClick = useCallback(() => {
    if (onActivate) {
      onActivate();
      return;
    }
    // Placeholder: 본 단계에서는 실제 API 호출 없음.
    alert("Emergency Stop: 아직 백엔드와 연결되지 않음 (paper mode only)");
  }, [onActivate]);

  return (
    <div
      className={`emergency-stop-bar${active ? " emergency-stop-bar-active" : ""}`}
      role="region"
      aria-label="Emergency Stop"
    >
      <div className="emergency-stop-text">
        <strong>Emergency Stop</strong>
        <span className="emergency-stop-note">
          paper mode 전용 — 실거래 미연결
        </span>
      </div>
      <button
        type="button"
        className="emergency-stop-btn"
        onClick={handleClick}
        aria-label="Emergency Stop"
      >
        {active ? "STOPPED" : "Emergency Stop"}
      </button>
    </div>
  );
}
