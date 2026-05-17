# Checklist Progress — 96항목 진척도

마지막 업데이트: 2026-05-17 (#14 Watchlist/Universe 보강 — 정규화·검증·universe cap·summary·seed CLI + docs/watchlist_universe.md. backend 1444 passed / 1 skipped. 15번 이후 미수정.)

상태: ☐ 미착수 · ◔ 부분 · ◑ 절반 · ◕ 거의 완료 · ✅ PASS

## Phase 0 — 방향/범위 (5)
| # | 항목 | 상태 | 산출물 |
|---:|---|:---:|---|
| 1 | Product Scope | ✅ | `docs/product_scope.md` (본문 + MOCA 허브 + MVP/제외/승격 + 회귀 테스트) |
| 2 | Strategy Portfolio | ✅ | `docs/strategy_portfolio.md` (4대 전략 카드 + 장세 매트릭스 + 김프 특수 정책 + 회귀 테스트) |
| 3 | Operating Modes | ✅ | `app/core/modes.py` (TradingMode + ModeCapability + allowed_transitions) + `docs/operating_modes.md` (행동 매트릭스 + 전환 그래프) + 회귀 테스트 |
| 4 | Safety Principles | ✅ | `docs/safety_principles.md` (12 섹션, 단일 경로 + 모듈 경계 + 강제 메커니즘 표) + `CLAUDE.md` + 회귀 테스트 43개 |
| 5 | Agent Trader Naming | ✅ | `app/core/app_info.py` (+ ReleaseNote), `app/api/info.py` (/api/app, /api/release-notes), `frontend/index.html` 브랜드 갱신 + 릴리즈 노트 모달, `frontend/src/appInfo.ts` placeholder + 회귀 테스트 21개 |

## Phase 1 — 시스템 아키텍처 (7)
| # | 항목 | 상태 | 산출물 |
|---:|---|:---:|---|
| 6 | Backend Skeleton | ✅ | `backend/app/{api,core,db,market,brokers,strategies,risk,execution,agents,governance,audit,schemas}` 구조 완료 |
| 7 | Frontend Skeleton | ✅ | Vite 5 + React 18 + TypeScript 5.5 + react-router 6. `frontend/{index.html,package.json,tsconfig.json,vite.config.ts,src/{main.tsx,App.tsx,api/{client,health,watchlist,audit}.ts,components/{Header,StatusCard}.tsx,pages/{Dashboard,Watchlist,Audit}Page.tsx,styles/global.css}}`. 빌드 검증 완료 (44 modules, 170 KB JS). 레거시 데모는 `legacy_demo.html` 로 보존. |
| 8 | Shared Schemas | ✅ | `app/schemas/{market,signal,order,position,risk,agent}.py` + `__init__.py` 단일 진입점. `is_order_intent: bool = False` 강제 (StrategySignal/PairSignal/KimpSignal/AgentDecision). `OrderRequest`/`OrderResult`/`Position`/`AccountSnapshot` 신규. 회귀 테스트 18개 (`test_schemas.py`) |
| 9 | Config Layer | ✅ | `app/core/config.py` + `Settings.summary()` (redaction) + `Settings.validate()` (운영 경고) + `ENV_VARS_REFERENCED` 카탈로그. REST: `GET /api/config/warnings` (공개), `GET /api/config/effective` (admin). `/api/status.safety_warnings` 추가. `.env.example` 파리티 회귀 테스트. `docs/config_layer.md` 설계 문서. YAML 미도입 (의도적). |
| 10 | Feature Flags | ✅ | `app/core/feature_flags.py` + 회귀 테스트 |
| 11 | Audit Foundation | ✅ | `app/audit/{audit_log,redaction,order_audit,agent_decision_log}.py`. AuditLog.record()가 redaction 자동 적용. OrderAuditLog는 lifecycle 매핑/검색, AgentDecisionLog는 is_order_intent 강제 기록. 회귀 테스트 18개 (`test_audit_foundation.py`) |
| 12 | CLAUDE.md | ✅ | 루트 CLAUDE.md |

## Phase 2 — 데이터/DB (7)
| # | 항목 | 상태 |
|---:|---|:---:|
| 13 | Database Schema | ✅ | `app/db/{models,session,migrations/}` + `alembic.ini`. 4개 공통 테이블(audit_events/orders/agent_decisions/positions). is_order_intent 기본 false 컬럼 강제. SQLAlchemy 2.0 + Alembic. **2026-05-17 추가**: 코인 전용 9개 테이블 (coin_symbol/coin_candle/coin_tick/coin_orderbook_snapshot/coin_signal/coin_order/coin_trade/coin_position/coin_risk_event) + `0003_crypto_schema.py` 마이그레이션 + `docs/crypto_database_schema.md`. 가격/수량 Numeric(28,12). CoinSignal.used_for_order 기본 False (advisory). CoinOrder.mode 기본 "PAPER" (LIVE 아님). secret 컬럼 부재 회귀. |
| 14 | Watchlist/Universe | ✅ | `WatchlistEntry` 모델 (list_name/symbol/exchange UNIQUE) + `app/market/watchlist.py` 서비스 (CRUD + list_names + remove_by_list) + `0002_watchlist.py` 마이그레이션 + REST API (`GET /api/watchlist`, `POST/DELETE/PATCH` admin 토큰). **2026-05-17 보강**: 정규화(symbol upper / exchange·list_name lower / strip) + 검증(빈/공백/길이/허용 거래소 화이트리스트) + universe 크기 cap (list_name별 default=50/majors=20/kimp_pairs=100 + 전체 `WATCHLIST_MAX_ENABLED_TOTAL` env, 기본 100) + `summary()` API 응답(by_exchange/by_list_name/limits) + seed 템플릿 `config/watchlists/{default,majors,kimp_pairs}.json` + 멱등 CLI `app.market.watchlist_seed`. 정책: `docs/watchlist_universe.md` (Watchlist 는 주문 허용 목록이 아니라 후보 universe 제한 장치 — RiskManager/OrderGuard/PermissionGate 통과 필수). |
| 15 | Market Data Collector | ✅ | `app/market/collector.py` (`MarketDataSource` Protocol + `MockMarketDataSource` 결정론 + `MarketDataCollector` ticker 캐시 + freshness 통합 + 예외 시 캐시 보존). REST: `GET /api/market/tickers`, `POST /api/market/collect` (admin, watchlist 기반). 거래소 SDK 직접 의존 없음 — 실제 source는 #21·#22에서 추가. |
| 16 | Data Freshness | ✅ |
| 17 | Data Quality | ✅ | `app/market/quality.py` (spread/volume/orderbook depth/top-size/fx anomaly/spike + `QualityReport.liquidity_ok`/`fx_anomaly_ok` Strategy 호환 플래그). CLI: `scripts/check_data_quality.py` (exit code 0/1/2). |
| 18 | Exchange Notices | ✅ | `app/market/notices.py` (Notice/NoticeRegistry/assess_symbol_notices/block_reasons + 6개 NoticeKind + 시간창 active 필터). REST: `GET/POST/DELETE /api/notices` + `GET /api/notices/symbol/{exchange}/{symbol}`. KimpStrategy의 `deposit_withdrawal_ok` 직결. |
| 19 | Trend/News/Theme Signals | ✅ | `app/market/themes.py` (`classify_regime` 순수 함수 + `ThemeRegistry` (와일드카드 거래소 + 다중 태그) + `NewsRegistry` (시간창 + 시장 전반/심볼별) + `assess_market_context` Agent dict). REST: 7개 엔드포인트. AgentOrchestrator의 context["regime"] 직결. |

## Phase 3 — 브로커/API (9)
| # | 항목 | 상태 |
|---:|---|:---:|
| 20 | Exchange Adapter Interface | ✅ | `app/brokers/base.py` 풀 구현: `ExchangeAdapter` ABC + `AdapterCapability` (READ_ONLY/PAPER/SANDBOX/LIVE) + capability 외 동작은 `ExchangeAdapterDisabledError`. MarketDataSource Protocol 호환 (collector 직접 주입). 출금 메서드 부재 회귀(`assert_no_withdrawal_methods`). 위반 시 테스트 실패. |
| 21 | Upbit Adapter | ✅ | `app/brokers/upbit_adapter.py` (READ_ONLY 영구 — pyupbit 공개 endpoint만, lazy import). 심볼 정규화 (BTC / BTC/KRW / KRW-BTC / BTC-KRW → KRW-BTC). API 키 생성자에서 거부. 테스트는 fake client 주입 (네트워크 호출 0). |
| 22 | OKX Adapter | ✅ | `app/brokers/okx_adapter.py` (READ_ONLY 영구 — ccxt 공개 endpoint, lazy import). 심볼 정규화 (BTC / BTC-USDT / BTC/USDT → BTC/USDT). API key/secret/passphrase 거부. `quoteVolume` → `volume_24h`, ms timestamp → UTC datetime. 테스트 fake client 주입 (네트워크 0). |
| 23 | Binance Adapter | ✅ | `app/brokers/binance_adapter.py` (READ_ONLY 영구 — ccxt spot endpoint, lazy import). 심볼 정규화 (BTC / BTCUSDT / BTC-USDT / BTC/USDT 모두 → BTC/USDT). API key/secret 거부. spot only — 선물은 #67. |
| 24 | Mock Broker | ✅ | `MockExchangeAdapter` — ExchangeAdapter contract 풀 구현, 결정론적 가격, 잔고 차감, FILLED/ACCEPTED 응답. mode='PAPER' 영구 고정 — LIVE 키 받지 않음. |
| 25 | Paper Broker | ✅ (이전부터) |
| 26 | API Rate Limit Guard | ✅ | `app/brokers/rate_limiter.py` (`TokenBucket` + `RateLimitExceeded`/`Timeout` + 거래소 프리셋 + `rate_limited` 데코레이터). 모의 시계로 테스트 — 실제 sleep 0. |
| 27 | Secret Permissions | ✅ | `docs/api_key_policy.md` (3-tier 권한 모델 + 거래소별 권한 매핑 + 키 발급/저장 규칙 + 사고 대응). `test_api_key_policy.py` 회귀: ENABLE_WITHDRAWAL 영구 false / 모든 어댑터 출금 메서드 부재 / .env.example 비어있음 / 정책↔코드 인용 정확성. |
| 28 | Sandbox/Paper Keys | ✅ | `docs/sandbox_paper_keys.md` 정책 + `Settings` 의 SANDBOX 키 슬롯 (OKX/Binance) + `Settings.validate()` 모드/키 정합성 경고 (PAPER에 LIVE키 / LIVE+SANDBOX 동시) + `.env.example` 분리 + 회귀 테스트. |

## Phase 4 — 전략 (8)
| # | 항목 | 상태 |
|---:|---|:---:|
| 29 | StrategyBase | ✅ | `app/strategies/base.py` (`StrategyCapability` + `StrategyBase` Protocol + `StrategyRegistry` + `assert_signal_contract`). 4개 전략 클래스에 `capability` 클래스 속성 추가 (동작 변경 0). REST: `GET /api/strategies/catalog`. |
| 30 | Trend Following | ✅ | `app/strategies/trend_following.py` 분리 완료. `_indicators.py` (ema/sma/atr) + `_signals.py` (StrategySignal) 도 추출. backward compat re-export로 `app.strategies.strategies.TrendFollowingStrategy` 도 동일 객체 유지. 14개 회귀 테스트. |
| 31 | Volatility Breakout | ✅ | `app/strategies/volatility_breakout.py` 분리. backward compat re-export 유지. capability + StrategyBase 만족. 13개 회귀 테스트. |
| 32 | Pair Trading | ✅ | `app/strategies/pair_trading.py` 분리 + `PairSignal` → `_signals.py`. `strategies.py` 가 단순 재export 허브로 축소(클래스/def 정의 0). 5경로 동일 객체 검증. 12개 회귀 테스트. |
| 33 | Kimp Mean Reversion | ✅ |
| 34 | Kimp Formula | ✅ | `app/market/kimp.py` 단일 진리 소스 (`compute_kimp_pct(strict)` + `assess_kimp` + `breakeven_threshold_pct` + `expected_edge_pct` + `is_anomaly`). `KimpSnapshot.compute_kimp` (silent), `KimpMeanReversionStrategy.calculate_kimp` (strict) 가 본 모듈로 위임. 42개 회귀 테스트. |
| 35 | Kimp Guards | ✅ | `app/strategies/kimp_guards.py` (`GuardResult`/`EntryGuardsReport` + 7개 순수 함수 가드 + `evaluate_entry_guards` 7단계 평가). KimpStrategy 가 본 모듈에 위임. severity (`pass`/`hold`/`block`) 분리로 HOLD/BLOCKED 구분 명확. 새 `kimp_anomaly` 가드 추가 (±10% 이상치 차단). 40개 회귀 테스트. |
| 36 | Funding Cost Guard | ✅ | `app/market/funding.py` (기여도/누적/연환산/이상치/방향). `kimp_guards.guard_funding_extreme` + `guard_funding_direction` + `evaluate_entry_guards` 가 8단계로 확장 (funding_extreme 가드 포함). KimpStrategy 가 funding_rate_pct 를 가드에 전달. 40개 회귀 테스트. |

## Phase 5 — Agent (10)
| # | 항목 | 상태 |
|---:|---|:---:|
| 37 | Agent Architecture | ✅ | `app/agents/{base,anomaly,signal_quality,risk_officer,orchestrator}.py` (AgentBase Protocol + AgentCapability + AgentRegistry + 4 sub-agent). Orchestrator 가 4단계 파이프라인 위임 + `decide_with_pipeline` 단계별 보고. RiskOfficer 최종 거부권 + WATCH_ONLY 도입. REST: `GET /api/agents/catalog`. |
| 38 | Risk Officer Agent | ✅ | RiskOfficerAgent + 신규 가드 (emergency_stop / position 한도 / order notional / leverage). `risk_context_from_manager(rm, order, account)` 헬퍼로 RiskManager 통합. ENTRY 액션만 포지션 한도 적용. 32개 회귀 테스트 (e2e 포함). |
| 39 | Signal Quality Agent | ✅ | `SignalQualityAgent` boosted — `QualityBreakdown` 분해 + QualityReport(#17)/news_severity(#19)/freshness/kimp_anomaly_hint 통합. block 뉴스 -30 / warn -10 / freshness stale -10. 32개 회귀 테스트. |
| 40 | Anomaly Agent | ✅ | `AnomalyAgent` boosted — Quality(#17)/Notices(#18)/News(#19)/Kimp(#34) 통합 hard veto. `anomaly_context_for(symbol, exchange, *, notices, themes, news, quality_report, freshness_stale, kimp_pct)` 헬퍼. SignalQualityAgent 의 -30 페널티와 별개로 block 뉴스 즉시 차단. 32개 회귀 테스트. |
| 41 | Explain Agent | ✅ | `ExplainAgent` — `explain_signal`/`explain_decision`/`explain_pipeline` (short/full/markdown 포맷). 액션 한국어 라벨 매핑 (BUY→매수, OPEN_REVERSE_KIMP→역김프 진입 후보). risk_veto 시 ⛔ 표시. ctx 보조 정보 (regime/themes/volume_surge/freshness/kimp). 34개 회귀 테스트. |
| 42 | Daily Report Agent | ✅ | `DailyReportAgent` — AuditLog 이벤트 집계 → `DailyReport(OrderSummary, AgentSummary, key_events)`. 시간 범위 필터 (since/until, 기본 오늘 자정). markdown/plain 렌더링. 28개 회귀 테스트 (e2e 시나리오 포함). |
| 43 | Theme Insight Agent | ✅ | `ThemeInsightAgent` — 심볼별 테마/뉴스/공지/김프 통합 브리핑. `SymbolBriefing` (themes/news_severity/headlines/tradable/notice_reasons/kimp_anomaly/overall_outlook). markdown/plain 렌더링 + outlook 이모지. 31개 회귀 테스트. |
| 44 | Loss Tagging Agent | ✅ | `LossTaggingAgent` — `TradeOutcome` → `LossAnalysis` (10개 카테고리: STOP_LOSS/TIME_STOP/SLIPPAGE/SPREAD/REGIME_CHANGE/KIMP_DIVERGENCE/NEWS_SHOCK/FUNDING_BURN/FEE_HEAVY/UNKNOWN). primary + contributing 태그. markdown/plain 렌더링. 35개 회귀 테스트. |
| 45 | Performance Agent | ✅ | `PerformanceAgent` — 거래 시퀀스 → `PerformanceMetrics` (총 거래/승률/PnL/Profit Factor/Max Drawdown/best/worst). by_strategy 분해, by_loss_category (LossTaggingAgent 통합). window 슬라이싱. inf PF/MDD 정확 처리. 35개 회귀 테스트. |
| 46 | Compliance Agent | ✅ | `ComplianceAgent` — CLAUDE.md 안전 원칙 자동 점검. 9개 표준 check (ENABLE_WITHDRAWAL/출금 메서드/redaction/AgentDecision/RiskOfficer/모듈 경계/legacy import/frontend secrets/feature flag). Settings.validate() 통합. fatal/warning 분리 + 🟢🟡🔴 emoji 렌더링. 28개 회귀 테스트. |

## Phase 6 — 리스크/주문실행 (13)
| # | 항목 | 상태 |
|---:|---|:---:|
| 47 | RiskManager | ✅ |
| 48 | Position Limit | ✅ (manager 내부) |
| 49 | Loss Limit | ✅ (manager 내부) |
| 50 | Kill Switch | ✅ (manager + API) |
| 51 | Order Guard | ✅ | `app/execution/order_guard.py` (`OrderGuard` + `OrderGuardResult`). 7개 검사 — 필수 필드/notional/leverage/action 화이트리스트/symbol 형식+blacklist/source 화이트리스트. OrderGateway 가 RiskManager 통과 후 호출. 53개 회귀 테스트. |
| 52 | AI Permission Gate | ✅ |
| 53 | Order Gateway | ✅ |
| 54 | OrderExecutor | ✅ | `app/execution/order_executor.py` (`OrderExecutor` Protocol + `PaperExecutor`/`ShadowExecutor`/`LiveExecutor`). OrderGateway 가 route 별 dict 로 위임. 외부 주입 가능 (테스트 mock). |
| 55 | Manual Approval | ✅ |
| 56 | PaperTrader | ✅ |
| 57 | Live Shadow | ✅ | `ShadowExecutor` 구현 — 주문 송신 없이 audit 만 기록. LIVE_SHADOW 모드에서 PermissionGate 가 route='shadow' 결정 시 호출. |
| 58 | AI Assist | ✅ | `ApprovalItem` 에 `source`/`agent_explain` 필드 추가. `ApprovalQueue.add(source=..., agent_explain=...)` + `pending_by_source('ai')` 필터. OrderGateway 가 source='ai' 를 ApprovalItem 에 패스스루. |
| 59 | AI Execution Gate | ✅ | `app/risk/ai_execution_gate.py` (`AIExecutionGate` + `AIGateResult`). 4개 임계값: `min_confidence`(0.75)/`min_quality_score`(80)/`max_daily_orders`(50)/`per_symbol_cooldown_sec`(15분). OrderGateway 가 source='ai' + route='live' 시에만 호출. `record_executed` 후 카운터/쿨다운 갱신. |

## Phase 7 — 백테스트/승격 (7)
| # | 항목 | 상태 |
|---:|---|:---:|
| 60 | Backtest Engine | ✅ | `app/backtest/engine.py` (`BacktestRunner` + `BacktestBar`/`BacktestSignal`/`BacktestResult`). 단일 포지션 이벤트 루프, 슬리피지/수수료, equity curve, TradeOutcome 출력. |
| 61 | Metrics | ✅ | `app/backtest/metrics.py` `compute_metrics` — `PerformanceAgent` 위임 + equity curve MDD + sharpe-like. `BacktestMetrics` 데이터 클래스. |
| 62 | Walk-forward | ✅ | `app/backtest/walk_forward.py` (`WalkForwardRunner` — expanding/rolling 모드, N-fold, min_fold_bars). 폴드별 metrics + avg_*. |
| 63 | Monte Carlo | ✅ | `app/backtest/monte_carlo.py` (`MonteCarloRunner` — bootstrap with replacement, seed 결정론, p05/p50/p95 percentile). |
| 64 | Promotion Gate | ✅ | `check_manual_approval_gate` (LIVE_MANUAL → LIVE_AI_ASSIST) — 6개 기준 (운영 기간/승인 건수/응답 시간/거부율/연속 손실/Compliance). |
| 65 | Paper Gate | ✅ (`check_paper_gate`) |
| 66 | AI Assist Gate | ✅ | `check_ai_execution_gate` (LIVE_AI_ASSIST → LIVE_AI_EXECUTION) — 6개 기준 (AI 보조 기간/처리 건수/사람 오버라이드율/AI Sharpe/MDD/Compliance). |

## Phase 8 — 선물 (6)
| # | 항목 | 상태 |
|---:|---|:---:|
| 67-72 | Futures Scope/Adapter/Margin/Strategy/UI/Gate | ☐ (의도적 후순위) |

## Phase 9 — 웹/PWA/배포 (11)
| # | 항목 | 상태 |
|---:|---|:---:|
| 73 | Agent-first Dashboard | ✅ | DashboardPage 가 StatusCard + KillSwitchButton + ApprovalQueueWidget 통합. 5초 폴링으로 라이브 상태 표시. |
| 74 | Approval UI | ✅ | `pages/ApprovalsPage.tsx` + `api/approvals.ts` + `components/ApprovalQueueWidget.tsx`. 승인/거부 + source 필터 (all/ai/manual) + AI agent_explain 표시. |
| 75 | Risk Panel | ✅ | `pages/RiskPage.tsx` — 시스템 상태/Kill Switch/safety_warnings(#9)/승격 게이트 visual. `api/risk.ts` (kill_switch + promotion gates). |
| 76 | PWA | ✅ | `public/manifest.json` + `public/sw.js` (cache-first 정적 / network-only `/api/*`). `index.html` 에 manifest 링크 + theme-color. main.tsx 에서 production 빌드만 SW 등록. |
| 77 | Notifications | ☐ | (별도 PR 권장 — Telegram 통합은 §2.1 secret 규제와 신중 검토) |
| 78 | Frontend Integration | ✅ | 전체 UI 가 backend `/api/*` 와 통합 (status/freshness/watchlist/audit/approvals/risk). 5초/60초 폴링. |
| 79 | Pages Demo | ◕ | `docs/deployment_github_pages_demo.md` 존재. mock data 모드는 후속. |
| 80 | Admin Login | ✅ | `contexts/AdminTokenContext.tsx` localStorage 기반 + `components/AdminTokenInput.tsx` 헤더 우측. 비-secret bundle 보장. |
| 81 | Tailscale | ◕ | `docs/deployment_mobile_tailscale.md` 존재. 실제 배포 검증은 운영자 수동 단계. |
| 82 | Tauri | ☐ | (별도 PR — 데스크탑 빌드는 노드/Rust 의존성 큼) |
| 83 | Auto Update | ✅ | `components/VersionWatcher.tsx` — `/api/app` 60초 폴링, 새 버전 감지 시 알림 배너. 자동 reload 안 함 (작업 중 방해 방지). |

## Phase 10 — 테스트/운영 (10)
| # | 항목 | 상태 | 산출물 |
|---:|---|:---:|---|
| 84 | Unit Tests | ✅ | pytest 1302 통과 / 1 skip (2026-05-15) |
| 85 | Integration Tests | ◔ | `backend/tests/integration/test_e2e_kimp_signal_to_audit.py` 1건 — 추가 시나리오 후속 |
| 86 | Staging | ☐ | (실거래 승격 단계 — paper-only 운영 중에는 비활성) |
| 87 | Audit Log | ◕ | 메모리 + CSV + redaction 자동 적용. DB 영속화는 `AuditEvent` 모델 존재, 라우팅 통합 후속 |
| 88 | Backup | ✅ | `scripts/backup_audit.py` (logs/ → 타임스탬프 폴더 + keep-days 보존) + 회귀 테스트 |
| 89 | Monitoring | ✅ | `/api/metrics` (JSON) + `/api/metrics/prom` (Prometheus) + `/api/healthz` |
| 90 | MVP Gate | ✅ | `scripts/mvp_gate.py` — compliance/required_docs/frontend_dist + `--skip-tests` 옵션. 2026-05-15 PASS |
| 91 | Pre-market Checklist | ✅ | `scripts/pre_market_checklist.py` — fatal/warning 분리, JSON 출력. 2026-05-15 9/10 통과 (ADMIN_TOKEN 기본값 warning) |
| 92 | Release Notes | ✅ | `docs/release_notes_template.md` (추가/개선·안전·정책·호환성·알려진 이슈·검증 섹션) |
| 93 | Security Scan | ✅ | `scripts/security_scan.py` (6개 정규식 패턴 + `# noqa: security-scan` per-line opt-out). 2026-05-15 0 finding |

## Phase 11 — 분석 고도화 (3)
| # | 항목 | 상태 |
|---:|---|:---:|
| 94-96 | Alpha Decay / Correlation Guard / Loss Tagging | ☐ |

---

## Step A 완료 사항 (2026-05-10)
- 디렉토리 정렬: `audit/`, `governance/`, `brokers/`, `schemas/`, `db/`, `api/` 신설
- `storage/` → `audit/`, `promotion/` → `governance/`, `risk/approval_queue` → `execution/`, `execution/paper_broker` → `brokers/`, `market/models` → `schemas/market` 이동
- `main.py` → `api/` 라우터 분리 (slim main + 7 라우터)
- 격리: `backend/_legacy_innogrit/` (분석/포지션매니저/utils/trade_managers/exit_engine/websocket_feed/quotes_guard)
- 신규: `CLAUDE.md`, `core/feature_flags.py`, `core/app_info.py`, `brokers/{base,mock_broker}.py`, 4개 smoke 테스트
- 56/56 테스트 통과 (모듈 경계 + 기본 false 회귀 포함)

## 다음 작업 우선순위 (Step B 진행 중)
- [x] **#1 Product Scope** — 완료 (2026-05-10)
- [x] **#2 Strategy Portfolio** — 완료 (2026-05-10)
- [x] **#3 Operating Modes** — 완료 (2026-05-10)
- [x] **#4 Safety Principles** — 완료 (2026-05-10)
- [x] **#5 Agent Trader Naming** — 완료 (2026-05-10) — **Phase 0 (방향/범위) 5/5 전체 완료**
- [x] **#8 Shared Schemas** — 완료 (2026-05-10) — signal/order/position/risk/agent 단일 진입점 + is_order_intent 강제
- [x] **#11 Audit Foundation** — 완료 (2026-05-10) — redaction + OrderAuditLog + AgentDecisionLog
- [x] **#13 Database Schema** — 완료 (2026-05-10) — SQLAlchemy 2.0 + Alembic + 4 테이블
- [x] **#14 Watchlist/Universe** — 완료 (2026-05-10) — watchlist 테이블 + Service + REST API
- [x] **#7 Frontend Skeleton** — 완료 (2026-05-10) — Vite/React/TS, 3 페이지, 4 API 모듈
- [x] **#15 Market Data Collector** — 완료 (2026-05-10) — collector + Mock source + REST + freshness 통합
- [x] **#17 Data Quality** — 완료 (2026-05-10) — quality.py + CLI script
- [x] **#18 Exchange Notices** — 완료 (2026-05-10) — notices.py + REST + KimpStrategy 직결 플래그
- [x] **#19 Trend/News/Theme Signals** — 완료 (2026-05-10) — themes.py + classify_regime + 통합 컨텍스트
- [x] **#9 Config Layer** — 완료 (2026-05-10) — summary/validate + REST + .env.example 파리티
- [x] **#20 Exchange Adapter Interface** — 완료 (2026-05-10) — capability 모델 + MockExchangeAdapter + 출금 부재 회귀
- [x] **#24 Mock Broker** — 완료 (2026-05-10) — MockExchangeAdapter (ExchangeAdapter 풀 구현)
- [x] **#21 Upbit Adapter** — 완료 (2026-05-10) — READ_ONLY 영구, pyupbit 공개 API, lazy import
- [x] **#22 OKX Adapter** — 완료 (2026-05-10) — READ_ONLY 영구, ccxt 공개 endpoint, lazy import
- [x] **#23 Binance Adapter** — 완료 (2026-05-10) — READ_ONLY 영구, ccxt spot, native 심볼 분리 지원
- [x] **#26 API Rate Limit Guard** — 완료 (2026-05-10) — TokenBucket + 프리셋 + 데코레이터
- [x] **#27 Secret Permissions** — 완료 (2026-05-10) — api_key_policy.md + 회귀 테스트
- [x] **#28 Sandbox/Paper Keys** — 완료 (2026-05-10) — sandbox_paper_keys.md + Settings SANDBOX 슬롯 + validate 정합성

**Phase 3 (브로커/API) 9/9 ✅ 전체 완료**
- [x] **#29 StrategyBase** — 완료 (2026-05-10) — capability + Protocol + Registry + catalog API
- [x] **#30 Trend Following** — 완료 (2026-05-10) — trend_following.py + _indicators + _signals
- [x] **#31 Volatility Breakout** — 완료 (2026-05-10) — volatility_breakout.py 분리
- [x] **#32 Pair Trading** — 완료 (2026-05-10) — pair_trading.py + PairSignal 이동, strategies.py 재export 허브화

**Phase 4 (전략) 분리 라인 완료** — #29~#33 모두 ✅. 다음은:
- [x] **#34 Kimp Formula** — 완료 (2026-05-10) — `app/market/kimp.py` 단일 진리 소스 + 부가 계산
- [x] **#35 Kimp Guards** — 완료 (2026-05-10) — kimp_guards.py + strategy 위임 + kimp_anomaly 추가
- [x] **#36 Funding Cost Guard** — 완료 (2026-05-10) — funding.py + 2개 가드 + evaluate_entry_guards 8단계 확장

**Phase 5 (Agent) 시작** — 다음은 개별 Agent 보강:
- [x] **#37 Agent Architecture** — 완료 (2026-05-10) — AgentBase + 4 sub-agent + 파이프라인 + catalog API
- [x] **#38 Risk Officer Agent** — 완료 (2026-05-10) — 7개 가드 + risk_context_from_manager 헬퍼 + e2e 통합
- [x] **#39 Signal Quality Agent** — 완료 (2026-05-10) — QualityBreakdown + QualityReport/news/freshness/kimp 통합
- [x] **#40 Anomaly Agent** — 완료 (2026-05-10) — Quality/Notices/News/Kimp hard veto 통합 + anomaly_context_for 헬퍼
- [x] **#41 Explain Agent** — 완료 (2026-05-10) — explain_signal/decision/pipeline + 3 포맷 + 한국어 액션 라벨
- [x] **#42 Daily Report Agent** — 완료 (2026-05-10) — AuditLog 집계 + OrderSummary/AgentSummary/key_events + markdown/plain 렌더링
- [x] **#43 Theme Insight Agent** — 완료 (2026-05-10) — 심볼별 통합 브리핑 (테마/뉴스/공지/김프) + outlook 분류
- [x] **#44 Loss Tagging Agent** — 완료 (2026-05-10) — 10개 카테고리 분류 + primary/contributing
- [x] **#45 Performance Agent** — 완료 (2026-05-10) — 승률/PnL/PF/MDD + 전략·손실 카테고리 분해
- [x] **#46 Compliance Agent** — 완료 (2026-05-10) — CLAUDE.md 9개 안전 원칙 자동 점검 + Settings 통합

**Phase 5 (Agent) 10/10 ✅ 전체 완료**

**Phase 6 (리스크/주문실행) 13/13 ✅ 전체 완료** (2026-05-10):
- ✅ #47 RiskManager / ✅ #48 Position Limit / ✅ #49 Loss Limit / ✅ #50 Kill Switch
- ✅ #51 Order Guard / ✅ #52 AI Permission Gate / ✅ #53 Order Gateway / ✅ #54 OrderExecutor
- ✅ #55 Manual Approval / ✅ #56 PaperTrader / ✅ #57 Live Shadow
- ✅ #58 AI Assist / ✅ #59 AI Execution Gate

**Phase 7 (백테스트/승격) 7/7 ✅ 전체 완료** (2026-05-10):
- ✅ #60 Backtest Engine / ✅ #61 Metrics / ✅ #62 Walk-forward / ✅ #63 Monte Carlo
- ✅ #64 Promotion Gate / ✅ #65 Paper Gate / ✅ #66 AI Assist Gate

**Phase 9 (웹/PWA/배포) 9/11 ✅** (2026-05-10):
- ✅ #73 Agent-first Dashboard / ✅ #74 Approval UI / ✅ #75 Risk Panel
- ✅ #76 PWA / ✅ #78 Frontend Integration / ✅ #80 Admin Login
- ✅ #83 Auto Update
- ◕ #79 Pages Demo (doc 존재, mock data 후속) / ◕ #81 Tailscale (doc 존재)
- ☐ #77 Notifications (Telegram — secret 규제로 별도 PR)
- ☐ #82 Tauri (데스크탑 빌드 — 별도 PR)
- **Phase 8 (선물)**: 의도적 후순위
- **Phase 9 (UI/PWA)**: #74 Approval UI, #75 Risk Panel, #76 PWA, #77 Notifications, #78 Frontend Integration
- **Phase 10 (운영/테스트)**: #85 Integration Tests, #86 Staging, #87 Audit Log 보강, #88 Backup, #89 Monitoring, #90 MVP Gate, #91 Pre-market, #92 Release Notes, #93 Security Scan
- **Phase 11 (분석 고도화)**: #94 Alpha Decay, #95 Correlation Guard, #96 Loss Tagging (#44에서 부분 구현)
- [ ] **#40 Anomaly Agent 보강** — 추출 완료, 데이터 품질(#17/#18) 통합 후속
- [ ] **#41–#46 기타 Agent** — Explain / Daily Report / Cost Tagging 등
- [ ] **(후속)** UpbitAdapter/OkxAdapter/BinanceAdapter 가 limiter 자동 적용하도록 통합
- [ ] **(후속)** Upbit/OKX/Binance 어댑터에 OHLCV 통합 — volume_24h/regime 정확도 향상
- [ ] **(후속)** ccxt 기반 어댑터 공통 로직 추상화 (BaseCcxtAdapter) — OKX/Binance 중복 제거
- [ ] **(후속)** Strategy/Collector 가 QualityReport·NoticeRegistry 를 직접 소비하도록 통합 — KimpStrategy 의 liquidity_ok/fx_anomaly_ok/deposit_withdrawal_ok 자동 채움 (별도 PR)
- [ ] **(후속)** NoticeRegistry DB 영속화 (현재 메모리)
- [ ] **#73 Agent-first Dashboard** — Header/StatusCard 위에 실제 위젯 (Kill Switch / Approval 큐)
- [ ] **#74 Approval UI** — React로 마이그레이션 (현재 placeholder만)
- [ ] **(후속)** OrderGateway/AgentOrchestrator를 신규 facade(OrderAuditLog/AgentDecisionLog)로 마이그레이션 — 동작 변경 없는 리팩터로 별도 PR
- [ ] **(후속)** AuditLog DB backing store 연동 — 메모리·CSV 외 DB 영속화 (`AuditEvent` 모델 활용)

## 2026-05-15 복구 세션 메모

- `cointrade.zip` 과 워킹트리 일치 확인 (260 파일) — 별도 복원 필요 없음
- `git init` + remote = `https://github.com/1976haru/coin.git` (cointrade 디렉토리 자체를 독립 저장소로)
- `.gitignore` 보강: `node_modules/`, `dist/`, `.pytest_cache/`, `cointrade.zip`, `*.db` 등 추가
- `security_scan.py` 에 `# noqa: security-scan` per-line opt-out 추가 — 가짜 토큰 fixture 2건 (`test_audit_foundation.py`, `test_phase10_scripts.py`) 합법화
- pytest 1302 통과 / 1 skip — 전체 그린
- `security_scan.py` finding 0 / `mvp_gate.py` PASS / `pre_market_checklist.py` warning 1 (ADMIN_TOKEN 기본값)

## 2026-05-17 1~12번 재검증 세션 메모

작업 범위: 체크리스트 1~12번 PASS 여부를 *파일 존재* 가 아닌 *실 테스트 결과* 로 재확인. 13번 이후는 손대지 않음.

### 1~12번 상태표 (실 테스트 기준)
| # | 항목 | 상태 | 검증 근거 |
|---:|---|:---:|---|
| 1  | Product Scope         | ✅ | `tests/test_docs_product_scope.py` 통과 |
| 2  | Strategy Portfolio    | ✅ | `tests/test_docs_strategy_portfolio.py` 통과 |
| 3  | Operating Modes       | ✅ | `tests/test_mode_capabilities.py` + `tests/test_modes_flags.py` 통과 |
| 4  | Safety Principles     | ✅ | `tests/test_docs_safety_principles.py` 통과 |
| 5  | Agent Trader Naming   | ✅ | `tests/test_app_info_and_branding.py` 통과 (브랜드 드리프트 회귀 포함) |
| 6  | Backend Skeleton      | ✅ | `tests/test_health.py` 통과 + 패키지 트리 정합 |
| 7  | Frontend Skeleton     | ✅ | `tests/test_frontend_skeleton.py` 통과 + `npm run typecheck` / `npm run build` / `npm test` 모두 통과 |
| 8  | Shared Schemas        | ✅ | `tests/test_schemas.py` 통과 (is_order_intent 강제 포함) |
| 9  | Config Layer          | ✅ | `tests/test_config_layer.py` + `tests/test_config.py` 통과 (redaction / validate / .env.example 파리티) |
| 10 | Feature Flags         | ✅ | `tests/test_feature_flags.py` 통과 (cross-test 모듈-식별자 오염 수정 후 안정) |
| 11 | Audit Foundation      | ✅ | `tests/test_audit_foundation.py` + `tests/test_audit_events_helpers.py` 통과 |
| 12 | CLAUDE.md             | ✅ | 루트 `CLAUDE.md` 존재 + 안전 원칙 회귀(`tests/test_docs_safety_principles.py`)에서 인용 검증 |

### 이번 세션에서 복구한 결함

1. **ApprovalQueue 타입힌트 잠재 버그** (`backend/app/execution/approval_queue.py`)
   - `def list(self) -> list[dict]:` 가 class body 내에서 `list` 이름을 메서드로 가리고,
     이후 `def pending(self) -> list[ApprovalItem]:` 의 annotation 이 `function not subscriptable`
     TypeError 를 발생 (Python 3.14 lazy annotation 시점). `inspect.signature`/`typing.get_type_hints`
     를 쓰는 외부 도구가 호출되면 즉시 깨짐.
   - 수정: 파일 상단에 `from __future__ import annotations` 추가 — annotation 을
     문자열로 보관하여 평가 시점을 늦춤. 동작 변경 0, 회귀 위험 0.

2. **Frontend typecheck 스크립트 오류** (`frontend/package.json`)
   - `tsc -b --noEmit` 는 TS5094: "Compiler option '--noEmit' may not be used with '--build'"
     오류로 항상 실패. project references 빌드 모드에서 `--noEmit` 은 의미가 모호하기 때문.
   - 수정: `typecheck` 를 `tsc -b` 로 단순화. 루트 `tsconfig.json` 은 이미 `noEmit: true`,
     참조 프로젝트 `tsconfig.node.json` 은 composite (output 보존 필요) 이라 그대로 둠.
   - 결과: `npm run typecheck`/`npm run build`/`npm test` 모두 그린.

3. **FeatureFlags 모듈-식별자 cross-test 오염** (`backend/app/core/feature_flags.py`, `backend/tests/test_modes_flags.py`)
   - `FeatureFlags` dataclass default 가 import 시점에 env 를 평가 (`_bool(...)` 호출)
     하던 구조 → 환경변수 변경을 반영하려면 `importlib.reload` 필요했음.
   - `test_modes_flags.py::_fresh_flags()` 가 `importlib.reload(app.core.feature_flags)` 호출 →
     `FeatureDisabledError` 클래스 객체가 재생성 → 이후 `test_feature_flags.py` 의
     `pytest.raises(FeatureDisabledError, ...)` 가 *과거 클래스* 를 잡으려 해 매칭 실패
     (`test_assert_live_trading_blocked_by_default` 등 3개 실패).
   - 수정:
     - `FeatureFlags` 필드를 `field(default_factory=lambda: _bool(...))` 로 전환 → env 는
       인스턴스 생성 시점에 평가. `importlib.reload` 불요.
     - `_fresh_flags()` 는 단순 `get_feature_flags()` 호출로 단축.
   - 결과: 단독 실행/병행 실행 모두 그린 (32/32).

### 검증 결과

**Backend** (`cd backend && python -m pytest -q`):
- 1404 passed / 1 skipped / 0 failed

**Backend focused (1~12번 범위)**:
```
python -m pytest \
  tests/test_docs_product_scope.py tests/test_docs_strategy_portfolio.py \
  tests/test_mode_capabilities.py tests/test_modes_flags.py \
  tests/test_docs_safety_principles.py tests/test_app_info_and_branding.py \
  tests/test_frontend_skeleton.py tests/test_schemas.py \
  tests/test_config_layer.py tests/test_config.py \
  tests/test_feature_flags.py tests/test_audit_foundation.py \
  tests/test_audit_events_helpers.py tests/test_health.py -q
```
- 349 passed / 1 skipped / 0 failed

**Frontend** (`cd frontend`):
- `npm run typecheck` — exit 0, 출력 없음 (그린)
- `npm run build` — `66 modules transformed`, `built in ~600ms`
- `npm test` — 4 tests passed (App.test.tsx)

### 작업 범위 확인

- 13번 Database Schema 이후 항목은 손대지 않음. 기존 상태 그대로 유지.
- 실거래 LIVE 주문 코드 추가 없음.
- `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` / `ENABLE_CRYPTO_FUTURES_LIVE` 기본값 변경 없음 (모두 default False 유지).
- frontend 에 secret/token/api key 추가 없음.
- Upbit/OKX/Binance 실거래 연동 확장 없음.
