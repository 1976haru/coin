# Mobile Access via Tailscale

체크리스트 #81.

## 목적
스마트폰에서 내 PC의 backend(local-only)를 안전하게 관제. **공개 인터넷 노출 금지**.

## 권장 구성
1. PC에 Tailscale 설치, 로그인 (개인 계정)
2. 스마트폰에 Tailscale 앱 설치, 같은 계정 로그인
3. PC backend를 모든 인터페이스에 바인드 (`uvicorn --host 0.0.0.0 --port 8000`)
4. 스마트폰에서 `http://<pc-tailscale-ip>:8000/` 접속
5. PWA 설치 (홈화면에 추가)

## 보안
- **포트 포워딩 절대 금지**. Tailscale 사설망(WireGuard 기반)만 사용.
- `ADMIN_TOKEN` 을 추측 어려운 값으로 변경.
- Tailscale ACL로 스마트폰 → PC backend 8000번만 허용 (선택).
- 내부망에서도 HTTPS 권장 (사설 CA 또는 Tailscale Funnel — 단 funnel은 공개되므로 비추천).

## 대안
- Cloudflare Zero Trust Tunnel (BYOK 인증 필수)
- WireGuard 직접 구성

## 작업 진행 순서
- [ ] 체크리스트 #80 Admin Login 완료 후 본 문서 업데이트
- [ ] 체크리스트 #76 PWA 완료 후 manifest 경로 추가
- [ ] 운영 가이드 (사용자용) 분리
