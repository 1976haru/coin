/**
 * Vite + React 진입점 — 체크리스트 #7 + #76 PWA.
 */
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import "./styles/global.css";

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("#root 엘리먼트를 찾을 수 없습니다.");

ReactDOM.createRoot(rootEl).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
);

// PWA service worker — 체크리스트 #76 (프로덕션 빌드만)
if ("serviceWorker" in navigator && import.meta.env.PROD) {
  window.addEventListener("load", () => {
    void navigator.serviceWorker.register("/sw.js").catch(() => {
      // SW 등록 실패는 무시 (네트워크/HTTPS 환경 의존)
    });
  });
}
