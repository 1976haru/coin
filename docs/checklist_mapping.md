# 체크리스트 매핑 — 96항목 ↔ 코드 경로

원본 체크리스트: `C:\아이디어\coin\agent_trader_crypto_os_v6_structure_checklist.xlsx` (시트: `02_통합체크리스트`)

각 항목의 진척 상태는 `docs/checklist_progress.md` 참조.

| # | Phase | 항목 | 산출물 (목표 경로) |
|---:|---|---|---|
| 1  | 0 방향/범위 | Product Scope | `docs/product_scope.md` |
| 2  | 0 | Strategy Portfolio | `docs/strategy_portfolio.md` |
| 3  | 0 | Operating Modes | `app/core/modes.py`, `docs/operating_modes.md` |
| 4  | 0 | Safety Principles | `docs/safety_principles.md`, `CLAUDE.md` |
| 5  | 0 | Agent Trader Naming | `app/core/app_info.py`, `frontend/src/appInfo.ts` |
| 6  | 1 시스템 아키텍처 | Backend Skeleton | `backend/app/{api,core,db,market,brokers,strategies,risk,execution,agents,governance,audit,schemas}` |
| 7  | 1 | Frontend Skeleton | `frontend/src/{pages,components,api,styles}` |
| 8  | 1 | Shared Schemas | `app/schemas/{market,signal,order,position,risk,agent}.py` |
| 9  | 1 | Config Layer | `app/core/config.py` |
| 10 | 1 | Feature Flags | `app/core/feature_flags.py` |
| 11 | 1 | Audit Foundation | `app/audit/{audit_log,order_audit,agent_decision_log,redaction}.py` |
| 12 | 1 | CLAUDE.md | `CLAUDE.md` |
| 13 | 2 데이터/DB | Database Schema | `app/db/{models,session,migrations/}` |
| 14 | 2 | Watchlist/Universe | `app/db/models.py` (watchlist 테이블) + `app/api/...` |
| 15 | 2 | Market Data Collector | `app/market/collector.py` |
| 16 | 2 | Data Freshness | `app/market/freshness.py` |
| 17 | 2 | Data Quality | `app/market/quality.py`, `scripts/check_data_quality.py` |
| 18 | 2 | Exchange Notices | `app/market/notices.py` |
| 19 | 2 | Trend/News/Theme Signals | `app/market/themes.py` (예정) |
| 20 | 3 브로커/API | Exchange Adapter Interface | `app/brokers/base.py` |
| 21 | 3 | Upbit Adapter | `app/brokers/upbit_adapter.py` |
| 22 | 3 | OKX Adapter | `app/brokers/okx_adapter.py` |
| 23 | 3 | Binance Adapter | `app/brokers/binance_adapter.py` |
| 24 | 3 | Mock Broker | `app/brokers/mock_broker.py` |
| 25 | 3 | Paper Broker | `app/brokers/paper_broker.py` |
| 26 | 3 | API Rate Limit Guard | `app/brokers/rate_limiter.py` (예정) |
| 27 | 3 | Secret Permissions | `docs/api_key_policy.md` |
| 28 | 3 | Sandbox/Paper Keys | `.env.example` profiles + 시작 가드 |
| 29 | 4 전략 | StrategyBase | `app/strategies/base.py` |
| 30 | 4 | Trend Following | `app/strategies/trend_following.py` |
| 31 | 4 | Volatility Breakout ATR | `app/strategies/volatility_breakout.py` |
| 32 | 4 | Pair Trading | `app/strategies/pair_trading.py` |
| 33 | 4 | Kimp/Reverse Kimp Strategy | `app/strategies/kimp_mean_reversion.py` |
| 34 | 4 | Kimp Formula | `app/market/kimp.py` (계산식 표준화) |
| 35 | 4 | Kimp Guards | `app/strategies/kimp_guards.py` |
| 36 | 4 | Funding Cost Guard | `app/risk/funding.py` |
| 37 | 5 Agent | Agent Architecture | `docs/agent_architecture.md`, `app/agents/base.py` |
| 38 | 5 | Market Observer | `app/agents/market_observer.py` |
| 39 | 5 | News/Trend Agent | `app/agents/news_risk_agent.py` |
| 40 | 5 | Risk Auditor | `app/agents/risk_officer.py` |
| 41 | 5 | Strategy Researcher | `app/agents/strategy_selector.py` |
| 42 | 5 | Execution Recommender | `app/agents/execution_recommender.py` |
| 43 | 5 | Daily Report Agent | `app/agents/report_writer.py` |
| 44 | 5 | Agent Memory | DB table `agent_memory` |
| 45 | 5 | Agent Operating Loop | `app/agents/orchestrator.py` (확장) |
| 46 | 5 | Agent Performance Score | `app/agents/performance.py` |
| 47 | 6 리스크/실행 | RiskManager | `app/risk/manager.py` |
| 48 | 6 | Position Limit | `app/risk/limits.py` |
| 49 | 6 | Loss Limit | `app/risk/limits.py` |
| 50 | 6 | Kill Switch | `app/risk/kill_switch.py` |
| 51 | 6 | Order Guard | `app/risk/order_guard.py` |
| 52 | 6 | AI Permission Gate | `app/risk/permission_gate.py` |
| 53 | 6 | Order Gateway | `app/execution/order_gateway.py` |
| 54 | 6 | OrderExecutor | `app/execution/route_order.py` |
| 55 | 6 | Manual Approval | `app/execution/approval_queue.py` |
| 56 | 6 | PaperTrader | `app/execution/paper_executor.py` |
| 57 | 6 | Live Shadow | `app/execution/shadow_executor.py` + DB shadow_trades |
| 58 | 6 | AI Assist | `app/agents/execution_recommender.py` (어시스트 모드) |
| 59 | 6 | AI Execution Gate | `app/governance/live_readiness.py` |
| 60 | 7 백테스트 | Backtest Engine | `app/backtest/engine.py` |
| 61 | 7 | Metrics | `app/backtest/metrics.py` |
| 62 | 7 | Walk-forward | `app/backtest/walk_forward.py` |
| 63 | 7 | Monte Carlo | `app/backtest/monte_carlo.py` |
| 64 | 7 | Promotion Gate | `app/governance/promotion_gates.py` |
| 65 | 7 | Paper Gate | `app/governance/promotion_gates.py::check_paper_gate` |
| 66 | 7 | AI Assist Gate | `app/governance/ai_assist_gate.py` |
| 67 | 8 선물 | Futures Scope | `docs/futures_scope.md` |
| 68 | 8 | Futures BrokerAdapter | `app/brokers/futures_base.py` |
| 69 | 8 | Margin Risk | `app/risk/margin_rules.py` |
| 70 | 8 | Futures StrategyBase | `app/strategies/futures/base.py` |
| 71 | 8 | Futures UI | `frontend/src/pages/FuturesPage.tsx` (feature flag로 숨김) |
| 72 | 8 | Futures Gate | `docs/futures_promotion_policy.md` |
| 73 | 9 웹/PWA | Agent-first Dashboard | `frontend/src/pages/DashboardPage.tsx` |
| 74 | 9 | Approval UI | `frontend/src/pages/ApprovalPage.tsx` |
| 75 | 9 | Risk Control Panel | `frontend/src/pages/RiskPage.tsx` |
| 76 | 9 | PWA | `frontend/public/manifest.webmanifest`, service worker |
| 77 | 9 | Notifications | `app/services/notifications.py` |
| 78 | 9 | Frontend Integration | `frontend/src/api/client.ts`, mockData.ts |
| 79 | 9 | GitHub Pages Demo | `.github/workflows/pages-deploy.yml` |
| 80 | 9 | Admin Login | `app/auth/*` |
| 81 | 9 | Local/Tailscale Access | `docs/deployment_mobile_tailscale.md` |
| 82 | 9 | Tauri Desktop App | `docs/desktop_app_packaging.md` |
| 83 | 9 | Auto Update Plan | `docs/auto_update_plan.md` |
| 84 | 10 테스트 | Unit Tests | `backend/tests/test_*` |
| 85 | 10 | Integration Tests | `backend/tests/test_order_flow.py` |
| 86 | 10 | Staging | `docker-compose.staging.yml` |
| 87 | 10 | Audit Log | `app/audit/audit_log.py` |
| 88 | 10 | Backup | `scripts/backup_db.ps1` |
| 89 | 10 | Monitoring | `app/services/monitoring.py` |
| 90 | 10 | MVP Gate | `docs/mvp_completion.md` |
| 91 | 10 | Pre-market Checklist | `scripts/pre_market_check.py` |
| 92 | 10 | Release Notes | `CHANGELOG.md` + frontend release notes |
| 93 | 10 | Security Scan | `.github/workflows/security-ci.yml` |
| 94 | 11 분석 | Alpha Decay | `app/analytics/alpha_decay.py` |
| 95 | 11 | Correlation Guard | `app/analytics/correlation_guard.py` |
| 96 | 11 | Loss Tagging | `app/analytics/loss_tagging.py` |
