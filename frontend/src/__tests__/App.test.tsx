/** 체크리스트 #7 기본 렌더링 회귀 테스트.
 *
 * - Dashboard 텍스트 렌더
 * - Emergency Stop 텍스트 존재
 * - Trading Mode "paper" 표시
 * - 주요 네비게이션 (Agent / Risk / Logs) 가시
 *
 * `fetch` / `localStorage` 를 사용하는 위젯(Header/Approval 등) 은 mocking 한다.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import App from "../App";

beforeEach(() => {
  // 위젯이 부팅 시 호출하는 /api/* 들은 빈 200 응답으로 무력화.
  // 실제 API 호출은 본 단계 검증 대상이 아님.
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      text: async () => "{}",
      json: async () => ({}),
    }),
  );
});

function renderApp() {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <App />
    </MemoryRouter>,
  );
}

describe("App skeleton — checklist #7", () => {
  it("renders Dashboard page on /", () => {
    renderApp();
    // h2 page title 또는 카드 제목 어느 쪽이든 "Dashboard" 가 보여야 한다.
    expect(screen.getAllByText(/Dashboard/i).length).toBeGreaterThan(0);
  });

  it("shows an Emergency Stop region", () => {
    renderApp();
    // 영문 라벨 또는 버튼 텍스트로 노출
    expect(screen.getAllByText(/Emergency Stop/i).length).toBeGreaterThan(0);
  });

  it("displays Trading Mode = paper on the dashboard", () => {
    renderApp();
    // ModeBadge 가 'PAPER' (대문자 라벨) 로 표시
    expect(screen.getAllByText(/PAPER/i).length).toBeGreaterThan(0);
    // 카드 설명에도 'paper' 가 노출되는지 보조 확인
    expect(screen.getAllByText(/paper/i).length).toBeGreaterThan(0);
  });

  it("exposes Agent / Risk / Logs navigation items", () => {
    renderApp();
    expect(screen.getAllByText(/Agent/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Risk/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Logs/i).length).toBeGreaterThan(0);
  });
});
