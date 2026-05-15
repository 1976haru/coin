# GitHub Pages Demo Deployment

체크리스트 #79.

## 원칙
GitHub Pages 는 **mock/demo UI 전용**. 실제 backend 데이터 흐름 금지.

## 무엇이 배포되나
- `frontend/` 빌드 결과 (Vite 정적 빌드)
- `frontend/src/api/mockData.ts` 의 mock 응답으로 모든 화면 시연
- 실제 거래소 호출 없음, secret 노출 없음

## 무엇은 절대 배포되지 않나
- backend FastAPI
- `.env`, API key, admin token
- 실제 사용자 데이터

## 배포 흐름 (예정)
1. `frontend/` 에서 `npm run build` (체크리스트 #7 완료 후)
2. `.github/workflows/pages-deploy.yml` 이 빌드 + Pages 게시
3. `appInfo.ts` 의 `isDemo=true` 분기로 mockData 사용
4. UI 상단에 **DEMO** 배지 영구 표시

## 안전 검증
- CI에 demo 빌드에서 backend URL이 절대값으로 들어가지 않는지 grep
- secret 스캔 (`security-ci.yml`)
- `isDemo` 가 false인 빌드는 Pages에 push 금지

## 현재 상태
- `frontend/index.html` 단일 파일 데모만 존재 (실제 backend 호출)
- React/Vite 마이그레이션은 #7
- Pages workflow는 #7 이후 작성
