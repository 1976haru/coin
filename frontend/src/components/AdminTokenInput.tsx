/** AdminTokenInput — 체크리스트 #80. 헤더 우측 admin 토큰 입력. */
import { useState } from "react";
import { useAdminToken } from "../contexts/AdminTokenContext";

export default function AdminTokenInput() {
  const { token, setToken, clearToken, hasToken } = useAdminToken();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");

  if (!editing) {
    return (
      <div className="admin-token-pill">
        {hasToken ? (
          <>
            <span className="admin-status">🔐 admin</span>
            <button
              className="link-btn"
              onClick={() => clearToken()}
              aria-label="logout"
            >
              로그아웃
            </button>
          </>
        ) : (
          <button
            className="link-btn"
            onClick={() => {
              setDraft(token);
              setEditing(true);
            }}
          >
            admin 로그인
          </button>
        )}
      </div>
    );
  }

  return (
    <form
      className="admin-token-form"
      onSubmit={(e) => {
        e.preventDefault();
        setToken(draft.trim());
        setEditing(false);
      }}
    >
      <input
        type="password"
        placeholder="X-Admin-Token"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        autoFocus
        aria-label="admin token"
      />
      <button type="submit">저장</button>
      <button type="button" onClick={() => setEditing(false)}>
        취소
      </button>
    </form>
  );
}
