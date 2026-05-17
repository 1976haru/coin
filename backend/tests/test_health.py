"""/api/health, /api/status smoke — 라우터 등록 + 안전 응답 형식 검증.

체크리스트 #6 Backend Skeleton: /api/health 가 status=ok, mode=paper 를 반환.
체크리스트 기존 항목: /api/status 가 안전 플래그를 노출.
"""
from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


# ── 체크리스트 #6: /api/health ────────────────────────────────────

def test_health_endpoint_returns_ok():
    r = client.get("/api/health")
    assert r.status_code == 200


def test_health_endpoint_status_is_ok():
    r = client.get("/api/health")
    assert r.json()["status"] == "ok"


def test_health_endpoint_service_name():
    r = client.get("/api/health")
    assert r.json()["service"] == "autotrade-backend"


def test_health_endpoint_default_mode_is_paper():
    """기본 trading mode 가 live 가 아니라 paper 여야 한다 (안전 기본값)."""
    r = client.get("/api/health")
    assert r.json()["mode"] == "paper"


# ── 기존 라우트 회귀 ──────────────────────────────────────────────

def test_root_returns_app_info_when_no_frontend(monkeypatch, tmp_path):
    """frontend 폴더 없으면 JSON 응답."""
    # CWD를 frontend가 없는 디렉토리로 (Pytest 실행 위치에 frontend 가 있을 수 있어 보호)
    import os
    monkeypatch.chdir(tmp_path)
    r = client.get("/")
    # FileResponse 또는 JSON 둘 다 200 이어야 함
    assert r.status_code == 200


def test_status_endpoint_exposes_required_fields():
    r = client.get("/api/status")
    assert r.status_code == 200
    d = r.json()
    for key in [
        "trading_mode", "mode_label", "demo_mode",
        "enable_live_trading", "enable_ai_execution",
        "risk_status", "pending_approvals", "audit_events", "app",
    ]:
        assert key in d, f"missing field: {key}"


def test_status_default_flags_are_false():
    r = client.get("/api/status")
    d = r.json()
    # 위험 플래그 기본 false 회귀
    assert d["enable_live_trading"] is False
    assert d["enable_ai_execution"] is False


def test_status_app_info_carries_name_and_version():
    r = client.get("/api/status")
    info = r.json()["app"]
    assert info["name"] == "Agent Trader Crypto OS"
    assert info["version"]
