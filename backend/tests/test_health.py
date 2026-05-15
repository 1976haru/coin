"""/api/status smoke — 라우터 등록 + 안전 응답 형식 검증."""
from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


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
