# Runbook

운영자가 일상적으로 수행하는 작업 + 비상 대응 절차.

## 매일

### 장 시작 전 (#91 pre-market)
- [ ] `/api/status` 확인 — 모드/플래그 정상
- [ ] `/api/freshness` — feed 신선
- [ ] 승인 대기 큐 비어있는지
- [ ] 전일 감사 로그 점검

### 장중
- 대시보드 모니터링
- Agent Council 알림 확인
- 위험 알림 (#77) 응답

### 장 종료 후
- DailyReportAgent 결과 검토 (#43)
- DB 백업 (#88)

## 비상 대응

### 데이터 지연
1. `/api/status` 의 `freshness_threshold_sec` 확인
2. WebSocket 재연결 시도
3. **Kill Switch 활성화** 고려 — `/api/kill-switch` (admin token)

### 비정상 손실
1. 즉시 Kill Switch 활성화
2. 모든 포지션 수동 점검 (해당 거래소 UI 직접)
3. RiskManager 일일 손실 한도 확인
4. 감사 로그 분석 (`/api/audit?limit=200`)

### LIVE 모드 사고
1. **즉시 모드 강등**: `TRADING_MODE=PAPER` + 재시작
2. ENABLE_* 플래그 모두 false 확인
3. 사후 분석 (`promotion_gates.check_reversion` 결과 확인)
4. 사용자/팀 보고

### 서비스 다운
1. `docker compose ps` / `docker compose logs backend`
2. 마지막 감사 이벤트 확인
3. 재시작
4. 재시작 후 첫 5분간 거래 차단 (warm-up)

## 환경변수 변경
- `.env` 수정 → backend 재시작 필요 (Settings 캐시)
- LIVE 관련 플래그 변경은 별도 PR + 사용자 승인 필요

## 비밀번호/토큰 회전
- `ADMIN_TOKEN` 분기당 1회 이상 회전
- 거래소 API 키는 docs/api_key_policy.md (작성 예정) 참조
- Telegram Token 노출 의심 시 즉시 재발급
