# `_legacy_innogrit/` — 격리 (Quarantine) 폴더

## 이 폴더는 무엇인가

작년 이노그릿(역김프 자동매매 봇) 프로토타입에서 가져온 코드를 보관한다. **활성 코드 트리 (`backend/app/`) 어디에서도 import하지 않는다.** 실수로 활성화되지 않도록 격리한다.

## 왜 즉시 삭제하지 않았나

1. 김프 계산, WebSocket 다중 종목 감시, 펀딩비 시뮬, VWAP 청산 등 **아이디어**는 새 시스템에서 흡수할 가치가 있다.
2. 사용자 지시: "기존 기능을 무작정 삭제하지 말고 먼저 백업/분류".
3. 체크리스트 진행 중 참고 자료로만 사용한다.

## 무엇이 들어있나

| 파일 | 원래 위치 | 새 시스템에서의 역할 |
|---|---|---|
| `analysis.py` | `backend/app/analysis.py` | 거래 성과 분석 (CSV 기반). #43 Daily Report Agent 작업 시 참고. |
| `position_manager.py` | `backend/app/position_manager.py` | 포지션 라이프사이클 + 안전장치. #47 RiskManager 강화/#56 PaperTrader 시 아이디어 차용. |
| `utils/logger.py` | `backend/app/utils/` | CSV/파일 로거. #87 Audit Log 강화 시 참고. |
| `utils/config_manager.py` | 〃 | 환율 캐시 + 설정. #9 Config + #15 Market Data Collector 시 참고. |
| `utils/notifier.py` | 〃 | Telegram 알림. #77 Notifications 시 참고. |
| `utils/vwap.py` | 〃 | 호가 기반 평균체결가. #54 Order Guard / #56 PaperTrader 강화 시 참고. |
| `utils/async_utils.py` | 〃 | 비동기 헬퍼. 일반 참고. |
| `execution/trade_manager_base.py` | `backend/app/execution/` | 추상 거래 매니저. #20 Exchange Adapter Interface와 무관 (구식 패턴). |
| `execution/trade_manager_demo.py` | 〃 | 데모 모드 매니저 (펀딩비/슬리피지 시뮬). #25 Paper Broker 고도화 시 참고. |
| `execution/trade_manager_live.py` | 〃 | ⚠️ **OKX 실거래 매니저**. ccxt + 환경변수 OKX_API_KEY 직접 사용. **활성화 금지**. |
| `execution/exit_engine.py` | 〃 | VWAP 기반 청산 의사결정. #56 PaperTrader 시 참고. |
| `market/websocket_feed.py` | `backend/app/market/` | Upbit/OKX WebSocket. #15 collector.py 작성 시 참고. |
| `market/quotes_guard.py` | 〃 | 양 거래소 시세 동기화 검사. #17 Data Quality 시 참고. |
| `config/config.json` | `cointrade/config/` | innogrit 트레이딩 룰 (entry_kimp_rate, leverage 등). 활성 코드에서 미사용. 김프 전략 파라미터 튜닝 시 참고. |

## 사용 규칙

- 이 폴더의 파일을 `app.*` 로 import 금지.
- `backend/_legacy_innogrit/` 자체가 Python 패키지로 인식되지 않도록 `__init__.py` 만들지 않음.
- 아이디어를 옮길 때는 새 모듈에 깔끔히 재구현. 단순 복사 금지.
- CI lint/test 대상에서 제외.

## 언제 완전히 지울 것인가

다음 모두 참이면 사용자 승인 후 삭제 가능:
- 체크리스트 #15, #17, #25, #43, #47, #56, #77, #87 모두 PASS
- 위 파일들의 모든 유용한 아이디어가 활성 코드 또는 docs로 이전됨
- 사용자 명시적 삭제 승인
