/**
 * VersionWatcher — 체크리스트 #83 Auto Update.
 *
 * /api/app 의 version 을 주기적으로 폴링해 빌드 시점 버전과 다르면 알림.
 * 사용자가 "새로고침"을 눌러야 새 버전 적용 (자동 reload 강제 안 함 — 작업 중일 수 있음).
 */
import { useEffect, useState } from "react";
import { fetchAppInfo, FALLBACK_APP_INFO } from "../appInfo";

export default function VersionWatcher() {
  const [currentVersion] = useState(FALLBACK_APP_INFO.version);
  const [serverVersion, setServerVersion] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const check = () => {
      fetchAppInfo()
        .then((info) => {
          if (!cancelled) setServerVersion(info.version);
        })
        .catch(() => undefined);
    };
    check();
    const id = setInterval(check, 60_000);  // 1분 간격
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  if (!serverVersion || serverVersion === currentVersion) return null;

  return (
    <div className="version-update-banner" role="alert">
      🔄 새 버전 v{serverVersion} 사용 가능 (현재 v{currentVersion}).
      <button
        className="link-btn"
        onClick={() => window.location.reload()}
      >
        새로고침
      </button>
    </div>
  );
}
