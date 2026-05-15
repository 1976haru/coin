# Release Notes — Agent Trader Crypto OS — 체크리스트 #92

## v{VERSION} ({YYYY-MM-DD})

### 추가/개선
- (예) #74 Approval UI — AI/manual source 필터 + agent_explain 표시

### 안전·정책
- (예) #59 AI Execution Gate — confidence/quality/일일 한도 임계값 추가

### 호환성·마이그레이션
- (예) Settings 에 `binance_api_key_sandbox` 슬롯 추가 — 기존 .env 영향 없음

### 알려진 이슈
- (예) Tauri 데스크탑 빌드 미지원

### 의존성 변경
- (예) `vite@^5.4.6` 추가

### 검증
- 단위 테스트: {N} passed
- ComplianceAgent fatal: 0
- frontend dist: 빌드 OK

---

## 작성 규칙

1. 한 항목 = 한 줄 (체크리스트 번호 인용)
2. 안전·정책 변경은 별도 섹션 — CLAUDE.md 영향 명시
3. 호환성 깨지는 변경은 마이그레이션 노트 필수
4. 검증 섹션은 실제 마지막 빌드 수치 (`scripts/mvp_gate.py --json`)
5. `app/core/app_info.py::RELEASE_NOTES` 와 동일 버전 매핑
