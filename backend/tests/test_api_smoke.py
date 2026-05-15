"""API smoke + 모듈 경계 회귀 테스트.

- 모든 라우터 경로가 등록되어 있는지
- AI Agent / Strategy 가 BrokerAdapter 를 직접 import하지 않는지
- _legacy_innogrit/ 가 활성 코드에서 import되지 않는지
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)

# ── 라우터 등록 ────────────────────────────────────────────────────

EXPECTED_ROUTES = {
    "/api/status",
    "/api/app",
    "/api/release-notes",
    "/api/release-notes/latest",
    "/api/freshness",
    "/api/strategies/kimp/signal",
    "/api/strategies/trend/signal",
    "/api/strategies/catalog",
    "/api/agents/catalog",
    "/api/order/preview",
    "/api/approval/queue",
    "/api/approval/{item_id}",
    "/api/kill-switch",
    "/api/promotion/paper-gate",
    "/api/promotion/shadow-gate",
    "/api/audit",
    "/api/watchlist",
    "/api/watchlist/{entry_id}",
    "/api/watchlist/{entry_id}/enable",
    "/api/watchlist/{entry_id}/disable",
    "/api/market/tickers",
    "/api/market/collect",
    "/api/notices",
    "/api/notices/{notice_id}",
    "/api/notices/symbol/{exchange}/{symbol}",
    "/api/market/context/{exchange}/{symbol}",
    "/api/themes",
    "/api/themes/tag",
    "/api/themes/tag/{theme}/{symbol}",
    "/api/news",
    "/api/news/{event_id}",
    "/api/config/warnings",
    "/api/config/effective",
}


def test_all_expected_routes_registered():
    paths = {r.path for r in app.router.routes}
    missing = EXPECTED_ROUTES - paths
    assert not missing, f"missing routes: {missing}"


def test_freshness_endpoint_returns_status():
    r = client.get("/api/freshness")
    assert r.status_code == 200
    d = r.json()
    assert "ok" in d
    assert "reason" in d


def test_kimp_signal_smoke():
    r = client.post("/api/strategies/kimp/signal", json={
        "symbol": "BTC",
        "upbit_price_krw": 138_000_000,
        "okx_price_usdt": 100_000,
        "usdt_krw": 1380,
    })
    assert r.status_code == 200
    body = r.json()
    assert "signal" in body and "agent" in body
    assert body["signal"]["action"] in {"OPEN_REVERSE_KIMP", "HOLD", "BLOCKED", "CLOSE"}


def test_order_preview_blocked_in_paper_default():
    """기본 PAPER 모드 → paper route 로 ACCEPT (또는 risk 차단)."""
    r = client.post("/api/order/preview", json={
        "symbol": "BTC/USDT", "side": "BUY",
        "notional_usdt": 10, "leverage": 1, "price": 100000,
    })
    assert r.status_code == 200
    d = r.json()
    assert d["status"] in {"ACCEPTED", "REJECTED", "BLOCKED", "PENDING_APPROVAL"}


def test_audit_requires_admin_token():
    """X-Admin-Token 없이는 401."""
    r = client.get("/api/audit")
    assert r.status_code == 401


def test_kill_switch_requires_admin_token():
    r = client.post("/api/kill-switch", json={"active": True, "reason": "test"})
    assert r.status_code == 401


# ── 모듈 경계 검증 ─────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_DIR   = REPO_ROOT / "backend" / "app"


def _grep_imports(
    folder: Path,
    forbidden_substrings: list[str],
    *,
    allow_in: list[str] | None = None,
) -> list[str]:
    """폴더 내 .py 에서 forbidden_substrings 중 하나라도 import 하는 파일 목록.

    allow_in: 상대 경로 substring 리스트. 매칭되는 파일에서는 forbidden 이어도 허용.
              (예: 거래소 어댑터 전용 디렉토리에서만 ccxt/pyupbit 허용)
    """
    allow_in = allow_in or []
    hits = []
    for py in folder.rglob("*.py"):
        rel = str(py.relative_to(REPO_ROOT)).replace("\\", "/")
        text = py.read_text(encoding="utf-8", errors="ignore")
        for s in forbidden_substrings:
            allowed_here = any(allow in rel for allow in allow_in if s in (
                "import pyupbit", "import ccxt", "from pyupbit", "from ccxt",
            ))
            for line in text.splitlines():
                stripped = line.strip()
                if (stripped.startswith("import ") or stripped.startswith("from ")) and s in stripped:
                    if allowed_here:
                        continue
                    hits.append(f"{py.relative_to(REPO_ROOT)}: {stripped}")
                    break
    return hits


def test_agents_do_not_import_brokers():
    """AI Agent 는 ExchangeAdapter / BrokerAdapter 를 직접 import 금지.

    예외: ComplianceAgent (#46) 는 검증 전용으로 brokers 를 inspect 만 함 — 거래
    결정에 사용하지 않는다 (lazy import + 출금 메서드 부재 검증).
    """
    hits = _grep_imports(
        APP_DIR / "agents",
        ["app.brokers", "app.execution.paper_executor",
         "app.execution.order_gateway"],
        allow_in=["backend/app/agents/compliance.py",
                  "backend\\app\\agents\\compliance.py"],
    )
    # allow_in 의 ccxt/pyupbit 화이트리스트 메커니즘이 substring 기준이므로
    # "app.brokers" 는 거기에 안 걸림 — compliance.py 만 직접 화이트리스트.
    hits = [h for h in hits if not (
        "compliance.py" in h and "app.brokers" in h
    )]
    assert not hits, f"agents imported broker layer: {hits}"


def test_strategies_do_not_import_brokers():
    hits = _grep_imports(APP_DIR / "strategies", ["app.brokers", "app.execution"])
    assert not hits, f"strategies imported broker/execution: {hits}"


def test_active_code_does_not_import_legacy():
    """활성 트리는 _legacy_innogrit 또는 utils.* (구식 logger 등) 를 import 금지.

    거래소 SDK(ccxt/pyupbit)는 ``app/brokers/*_adapter.py`` 안에서 lazy import 만 허용
    (체크리스트 #21·#22·#23). 그 외 위치에서는 여전히 금지.
    """
    hits = _grep_imports(
        APP_DIR,
        ["_legacy_innogrit", "from utils.", "import utils.",
         "import pyupbit", "import ccxt"],
        allow_in=["backend/app/brokers/upbit_adapter.py",
                  "backend/app/brokers/okx_adapter.py",
                  "backend/app/brokers/binance_adapter.py",
                  "backend\\app\\brokers\\upbit_adapter.py",
                  "backend\\app\\brokers\\okx_adapter.py",
                  "backend\\app\\brokers\\binance_adapter.py"],
    )
    assert not hits, f"active code touches legacy: {hits}"


def test_active_code_does_not_use_old_paths():
    """이전 위치(storage/, promotion/, market/models, risk/approval_queue, execution/paper_broker) import 금지."""
    hits = _grep_imports(APP_DIR, [
        "app.storage", "app.promotion",
        "app.market.models",
        "app.risk.approval_queue",
        "app.execution.paper_broker",
    ])
    assert not hits, f"active code uses old paths: {hits}"
