/** 헤더 — 앱 메타 정보 + 운용 모드 — 체크리스트 #5/#7. */
import { useEffect, useState } from "react";
import { fetchAppInfo, FALLBACK_APP_INFO, type AppInfo } from "../appInfo";
import { fetchStatus, type AppStatus } from "../api/health";

export default function Header() {
  const [info, setInfo] = useState<AppInfo>(FALLBACK_APP_INFO);
  const [status, setStatus] = useState<AppStatus | null>(null);

  useEffect(() => {
    fetchAppInfo().then(setInfo).catch(() => undefined);
    fetchStatus().then(setStatus).catch(() => setStatus(null));
  }, []);

  return (
    <header className="app-header">
      <div className="brand">
        <h1>{info.name}</h1>
        <span className="version">v{info.version}</span>
        {status && <span className={`mode mode-${status.trading_mode}`}>{status.trading_mode}</span>}
      </div>
      <p className="tagline">{info.tagline}</p>
    </header>
  );
}
