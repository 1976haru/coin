/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 체크리스트 #7 Frontend Skeleton — Vite 설정.
// 개발: /api/* → http://localhost:8000 (FastAPI) 프록시.
// 프로덕션 빌드: dist/ 출력 → FastAPI 가 /static 으로 마운트.
// 테스트: vitest + jsdom + @testing-library/react.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: true,
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
