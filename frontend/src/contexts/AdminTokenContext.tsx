/**
 * AdminTokenContext — 체크리스트 #80 Admin Login.
 *
 * admin 토큰을 localStorage 에 저장 + Context 로 모든 컴포넌트가 접근.
 * frontend bundle 에는 절대 박지 않음 (CLAUDE.md §2.1.5) — 사용자가 직접 입력.
 */
import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";

const STORAGE_KEY = "agent_trader_admin_token";

interface AdminTokenContextValue {
  token: string;
  setToken: (t: string) => void;
  clearToken: () => void;
  hasToken: boolean;
}

const AdminTokenContext = createContext<AdminTokenContextValue | null>(null);

export function AdminTokenProvider({ children }: { children: ReactNode }) {
  const [token, setTokenState] = useState<string>("");

  useEffect(() => {
    try {
      const saved = window.localStorage.getItem(STORAGE_KEY);
      if (saved) setTokenState(saved);
    } catch {
      // localStorage 차단 환경 (예: incognito 일부) — 무시
    }
  }, []);

  function setToken(t: string) {
    setTokenState(t);
    try {
      if (t) window.localStorage.setItem(STORAGE_KEY, t);
      else window.localStorage.removeItem(STORAGE_KEY);
    } catch {
      // 저장 실패해도 메모리에는 유지
    }
  }

  function clearToken() {
    setToken("");
  }

  return (
    <AdminTokenContext.Provider
      value={{ token, setToken, clearToken, hasToken: !!token }}
    >
      {children}
    </AdminTokenContext.Provider>
  );
}

export function useAdminToken(): AdminTokenContextValue {
  const ctx = useContext(AdminTokenContext);
  if (!ctx) throw new Error("useAdminToken must be inside AdminTokenProvider");
  return ctx;
}
