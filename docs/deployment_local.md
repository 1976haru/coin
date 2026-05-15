# Local Deployment

## 개발 환경
- Windows 10/11 + PowerShell 권장
- Python 3.11+ (3.14 호환 확인됨)
- Docker Desktop (선택)

## 1. 의존성 설치
```powershell
pip install -r backend/requirements.txt
```

## 2. 환경변수
```powershell
Copy-Item .env.example .env
# .env 편집 — TRADING_MODE는 PAPER 유지, ADMIN_TOKEN만 변경
```

## 3. 실행
```powershell
.\scripts\dev_backend.ps1
# → http://localhost:8000
```

또는 Docker:
```powershell
docker compose up -d
docker compose logs -f backend
```

## 4. 확인
```powershell
curl http://localhost:8000/api/status
```

응답에 `"trading_mode": "PAPER"`, `"enable_live_trading": false` 가 보이면 정상.

## 5. 테스트
```powershell
.\scripts\test_backend.ps1
```

## 안전 점검
시작 전 매번 확인:
- [ ] `.env` 파일이 git ignore에 들어있다
- [ ] OKX/Upbit 키가 비어있거나 read-only 권한
- [ ] `ADMIN_TOKEN` 이 기본값(`change-me-local-only`) 이 아니다
- [ ] `frontend/` 의 어떤 파일에도 secret 없음

## 트러블슈팅
- 포트 8000 충돌: `uvicorn ... --port 8001` 로 우회
- 한국어 출력 깨짐: 콘솔 codepage 65001 (`chcp 65001`)
- Windows에서 `.sh` 실행: `bash scripts/dev_start.sh` (Git Bash)
