"""체크리스트 #26 API Rate Limit Guard — 회귀 테스트.

검증 (fake clock + fake sleep — 실제 sleep 없음):
  Policies / parsers:
    1. RateLimitPolicy validation (capacity/refill/unit/max_retries)
    2. get_default_policy — known/unknown 거래소·그룹
    3. parse_retry_after — float / HTTP-date 미인식 / None
    4. parse_okx_error — code "0" → None, code "50011" / status 429
    5. parse_binance_used_weight — X-MBX-USED-WEIGHT / order count
    6. parse_upbit_remaining_req — backward-compat wrapper
  RateLimitGuard:
    7. can_call allowed 기본
    8. disabled policy → allowed=False
    9. safety_buffer 가 token 부족 판단에 반영
   10. cooldown 중 can_call=False + wait_seconds
   11. acquire 토큰 소비 / state.total_acquired 카운터
   12. update_from_response 가 upbit Remaining-Req → state.remaining 갱신
   13. update_from_response 가 binance X-MBX-USED-WEIGHT → state.used_weight 갱신
   14. update_from_response 가 OKX 50011 본문 → cooldown 진입
   15. update_from_error 429 → cooldown + RetryDecision should_retry=True
   16. update_from_error 418 → 더 긴 cooldown
   17. update_from_error 50011 → cooldown
   18. update_from_error 'auth' / 'invalid' → no-retry
   19. update_from_error 무한 재시도 방지 (max_retries 초과 → no-retry)
   20. exponential backoff 적용 + max_backoff_sec 상한
   21. Retry-After 헤더 우선 적용 (429 정책보다)
   22. reset() 가 state + bucket 초기화
   23. snapshot dict 형식
  Registry:
   24. ExchangeRateLimitRegistry.get 자동 생성
   25. register 로 커스텀 정책 주입
   26. snapshot_all 정렬 + count
   27. build_default_registry — preload 모든 정책
  REST API:
   28. GET /api/rate-limits 응답 구조
  정적 회귀:
   29. 본 모듈 / API 라우터에 ENABLE_LIVE_TRADING=True / while True 부재
   30. broker SDK import 부재
   31. brokers __all__ exports
"""
from __future__ import annotations
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.brokers import (
    RateLimitPolicy,
    list_default_policies, get_default_policy,
    parse_upbit_remaining_req, parse_okx_error,
    parse_binance_used_weight_canonical, parse_retry_after,
    AcquireDecision, RetryDecision, GuardState,
    RateLimitGuard, ExchangeRateLimitRegistry, build_default_registry,
    ERROR_KIND_429, ERROR_KIND_418, ERROR_KIND_OKX_50011,
    ERROR_KIND_NETWORK, ERROR_KIND_AUTH, ERROR_KIND_INVALID,
)


# ── 모의 시계 ────────────────────────────────────────────────────


class _FakeClock:
    """수동 진행 시계 + sleep 누적기 (실제 sleep 안 함)."""

    def __init__(self, start: float = 1000.0):
        self.now = float(start)
        self.sleep_calls: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleep_calls.append(float(seconds))
        self.now += max(0.0, float(seconds))

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


def _make_guard(
    *,
    exchange: str = "test", group: str = "default",
    capacity: float = 10, refill: float = 10.0,
    safety_buffer: int = 1,
    disabled: bool = False,
    unit: str = "req",
    cooldown_on_429: float = 5.0,
    cooldown_on_418: float = 60.0,
    cooldown_on_okx_50011: float = 3.0,
    cooldown_on_network: float = 0.5,
    max_retries: int = 2,
    base_backoff: float = 0.5,
    max_backoff: float = 8.0,
    clock: _FakeClock | None = None,
) -> tuple[RateLimitGuard, _FakeClock]:
    clk = clock or _FakeClock()
    p = RateLimitPolicy(
        exchange=exchange, group=group,
        capacity=capacity, refill_rate_per_sec=refill,
        safety_buffer=safety_buffer,
        unit=unit,
        disabled=disabled,
        cooldown_on_429_sec=cooldown_on_429,
        cooldown_on_418_sec=cooldown_on_418,
        cooldown_on_okx_50011_sec=cooldown_on_okx_50011,
        cooldown_on_network_sec=cooldown_on_network,
        max_retries=max_retries,
        base_backoff_sec=base_backoff,
        max_backoff_sec=max_backoff,
    )
    g = RateLimitGuard(p, time_fn=clk.time, sleep_fn=clk.sleep)
    return g, clk


# ── 1. RateLimitPolicy validation ────────────────────────────────


def test_policy_rejects_invalid_capacity():
    with pytest.raises(ValueError):
        RateLimitPolicy(exchange="x", group="y", capacity=0, refill_rate_per_sec=1)


def test_policy_rejects_invalid_unit():
    with pytest.raises(ValueError):
        RateLimitPolicy(exchange="x", group="y", capacity=1,
                        refill_rate_per_sec=1, unit="lol")


def test_policy_rejects_negative_max_retries():
    with pytest.raises(ValueError):
        RateLimitPolicy(exchange="x", group="y", capacity=1,
                        refill_rate_per_sec=1, max_retries=-1)


# ── 2. default policy lookup ─────────────────────────────────────


def test_default_policy_known():
    p = get_default_policy("upbit", "quotation")
    assert p.exchange == "upbit" and p.group == "quotation"
    assert p.capacity > 0


def test_default_policy_unknown_returns_conservative():
    p = get_default_policy("alien_exchange", "some_group")
    assert p.exchange == "alien_exchange"
    assert "default conservative" in p.notes


def test_list_default_policies_includes_all_exchanges():
    policies = list_default_policies()
    exchanges = {k[0] for k in policies}
    for ex in ("upbit", "okx", "binance", "mock", "paper"):
        assert ex in exchanges


def test_okx_private_and_trade_disabled():
    assert get_default_policy("okx", "private").disabled is True
    assert get_default_policy("okx", "trade").disabled is True


def test_binance_private_and_futures_disabled():
    assert get_default_policy("binance", "spot_private").disabled is True
    assert get_default_policy("binance", "futures").disabled is True


def test_upbit_exchange_group_disabled():
    assert get_default_policy("upbit", "exchange").disabled is True


# ── 3. parse_retry_after ─────────────────────────────────────────


def test_parse_retry_after_int_string():
    assert parse_retry_after({"Retry-After": "5"}) == 5.0


def test_parse_retry_after_float_string():
    assert parse_retry_after({"retry-after": "3.5"}) == 3.5


def test_parse_retry_after_negative_returns_none():
    assert parse_retry_after({"Retry-After": "-1"}) is None


def test_parse_retry_after_invalid_returns_none():
    assert parse_retry_after({"Retry-After": "not-a-number"}) is None
    assert parse_retry_after(None) is None
    assert parse_retry_after({}) is None


# ── 4. parse_okx_error ───────────────────────────────────────────


def test_parse_okx_error_ok_returns_none():
    assert parse_okx_error({"code": "0", "msg": "", "data": []}) is None


def test_parse_okx_error_50011_is_rate_limit():
    out = parse_okx_error({"code": "50011", "msg": "Requests too frequent"})
    assert out["is_rate_limit"] is True


def test_parse_okx_error_status_429_marks_rate_limit():
    out = parse_okx_error({"code": "0", "msg": ""}, status_code=429)
    assert out is not None
    assert out["is_rate_limit"] is True


def test_parse_okx_error_other_code_not_rate_limit():
    out = parse_okx_error({"code": "50000", "msg": "bad request"})
    assert out is not None
    assert out["is_rate_limit"] is False


# ── 5. parse_binance_used_weight (canonical) ─────────────────────


def test_parse_binance_used_weight_canonical():
    out = parse_binance_used_weight_canonical(
        {"X-MBX-USED-WEIGHT-1M": "23", "X-MBX-ORDER-COUNT-10S": "1"},
    )
    assert out["used_weight_1m"] == 23
    assert out["order_count_10s"] == 1


def test_parse_binance_used_weight_canonical_safe_for_none():
    assert parse_binance_used_weight_canonical(None) == {}


# ── 6. parse_upbit_remaining_req (canonical) ────────────────────


def test_parse_upbit_remaining_req_canonical():
    out = parse_upbit_remaining_req("group=market; min=599; sec=9")
    assert out["sec"] == 9 and out["group"] == "market"


# ── 7-9. can_call basic + safety_buffer + disabled ──────────────


def test_can_call_allowed_by_default():
    g, _ = _make_guard()
    dec = g.can_call()
    assert dec.allowed is True


def test_disabled_policy_blocks():
    g, _ = _make_guard(disabled=True)
    dec = g.can_call()
    assert dec.allowed is False
    assert "disabled" in dec.reason


def test_safety_buffer_blocks_when_insufficient_tokens():
    # capacity 2, safety_buffer 2 → need 1 + 2 = 3 토큰. 처음부터 부족.
    g, _ = _make_guard(capacity=2, refill=1.0, safety_buffer=2)
    dec = g.can_call()
    assert dec.allowed is False
    assert "insufficient" in dec.reason
    assert dec.wait_seconds > 0


# ── 10. cooldown 차단 ───────────────────────────────────────────


def test_cooldown_blocks_can_call():
    g, clk = _make_guard()
    g.update_from_error(ERROR_KIND_429)
    dec = g.can_call()
    assert dec.allowed is False
    assert "cooldown" in dec.reason
    assert dec.cooldown_remaining > 0
    # 시간 진행 후 다시 가능.
    clk.advance(seconds=10.0)
    dec2 = g.can_call()
    assert dec2.allowed is True


# ── 11. acquire 토큰 소비 + 카운터 ──────────────────────────────


def test_acquire_consumes_token_and_increments_counter():
    g, _ = _make_guard(capacity=5, refill=1.0)
    r = g.acquire(weight=1)
    assert r.allowed is True
    assert g.state.total_calls == 1
    assert g.state.total_acquired == 1


def test_acquire_during_cooldown_returns_throttled():
    g, _ = _make_guard()
    g.update_from_error(ERROR_KIND_429)
    r = g.acquire(weight=1)
    assert r.allowed is False
    assert g.state.total_throttled == 1


# ── 12. update_from_response → Upbit Remaining-Req ──────────────


def test_update_from_response_upbit_sets_remaining():
    g, _ = _make_guard(exchange="upbit", group="quotation")
    g.update_from_response(
        headers={"Remaining-Req": "group=market; min=599; sec=7"},
        status_code=200,
    )
    assert g.state.remaining == 7


# ── 13. update_from_response → Binance used weight ──────────────


def test_update_from_response_binance_sets_used_weight():
    g, _ = _make_guard(exchange="binance", group="spot_public")
    g.update_from_response(
        headers={"X-MBX-USED-WEIGHT-1M": "120"},
        status_code=200,
    )
    assert g.state.used_weight == 120


# ── 14. update_from_response → OKX 50011 cooldown ──────────────


def test_update_from_response_okx_50011_enters_cooldown():
    g, _ = _make_guard(exchange="okx", group="public",
                       cooldown_on_okx_50011=4.0)
    g.update_from_response(
        headers={},
        status_code=200,
        body={"code": "50011", "msg": "Requests too frequent"},
    )
    assert g.state.cooldown_until > 0
    assert g.state.total_okx_50011 >= 1


# ── 15. update_from_error 429 RetryDecision ─────────────────────


def test_update_from_error_429_should_retry_with_cooldown():
    g, clk = _make_guard(cooldown_on_429=5.0, max_retries=2)
    rd = g.update_from_error(ERROR_KIND_429)
    assert rd.should_retry is True
    assert rd.wait_seconds >= 5.0
    assert rd.attempt == 1
    assert g.state.total_429 == 1
    assert clk.sleep_calls == []  # guard 가 sleep 직접 호출하지 않음


# ── 16. update_from_error 418 → 더 긴 cooldown ─────────────────


def test_update_from_error_418_longer_cooldown():
    g, _ = _make_guard(cooldown_on_418=60.0, max_retries=2)
    rd = g.update_from_error(ERROR_KIND_418)
    assert rd.should_retry is True
    assert rd.wait_seconds >= 60.0
    assert g.state.total_418 == 1


# ── 17. update_from_error 50011 → cooldown ─────────────────────


def test_update_from_error_okx_50011_cooldown():
    g, _ = _make_guard(cooldown_on_okx_50011=3.0)
    rd = g.update_from_error(ERROR_KIND_OKX_50011)
    assert rd.should_retry is True
    assert rd.wait_seconds >= 3.0


# ── 18. no-retry kinds ─────────────────────────────────────────


def test_auth_error_no_retry():
    g, _ = _make_guard()
    rd = g.update_from_error(ERROR_KIND_AUTH)
    assert rd.should_retry is False
    assert "no-retry" in rd.reason


def test_invalid_error_no_retry():
    g, _ = _make_guard()
    rd = g.update_from_error(ERROR_KIND_INVALID)
    assert rd.should_retry is False


def test_error_kind_string_aliases_normalize():
    """'429' 문자열 등 별칭도 인식."""
    g, _ = _make_guard()
    rd = g.update_from_error("429")
    assert rd.should_retry is True
    assert g.state.total_429 == 1


# ── 19. max_retries 초과 → no-retry ─────────────────────────────


def test_max_retries_exceeded_blocks_retry():
    g, _ = _make_guard(max_retries=2)
    g.update_from_error(ERROR_KIND_429)   # attempt=1
    g.update_from_error(ERROR_KIND_429)   # attempt=2
    rd = g.update_from_error(ERROR_KIND_429)  # exceed
    assert rd.should_retry is False
    assert "max retries exceeded" in rd.reason


def test_no_infinite_retry_loop():
    """RetryDecision should_retry 가 결국 False 가 된다 (무한 루프 없음)."""
    g, _ = _make_guard(max_retries=3)
    decisions: list[RetryDecision] = []
    # 안전 상한 — 무한루프이면 이 테스트가 fail.
    for _ in range(20):
        rd = g.update_from_error(ERROR_KIND_429)
        decisions.append(rd)
        if not rd.should_retry:
            break
    assert any(not d.should_retry for d in decisions)


# ── 20. exponential backoff + max_backoff cap ──────────────────


def test_exponential_backoff_with_cap():
    g, _ = _make_guard(
        cooldown_on_429=0.0,   # cooldown 0 으로 backoff 만 보고 싶다
        base_backoff=1.0, max_backoff=4.0, max_retries=10,
    )
    waits: list[float] = []
    for _ in range(6):
        rd = g.update_from_error(ERROR_KIND_429)
        if not rd.should_retry:
            break
        waits.append(rd.wait_seconds)
    # 1, 2, 4, 4, 4, ...  (max_backoff=4 cap)
    assert waits[0] == pytest.approx(1.0)
    assert waits[1] == pytest.approx(2.0)
    assert waits[2] == pytest.approx(4.0)
    assert all(w <= 4.0 + 1e-9 for w in waits)


# ── 21. Retry-After 헤더 우선 ──────────────────────────────────


def test_retry_after_header_overrides_policy_cooldown():
    g, _ = _make_guard(cooldown_on_429=5.0)
    rd = g.update_from_error(
        ERROR_KIND_429,
        headers={"Retry-After": "12"},
    )
    assert rd.should_retry is True
    # 12 가 5 보다 크므로 wait >= 12
    assert rd.wait_seconds >= 12.0


# ── 22. reset() ─────────────────────────────────────────────────


def test_reset_clears_state_and_tokens():
    g, _ = _make_guard(capacity=2, refill=1.0)
    g.acquire()
    g.update_from_error(ERROR_KIND_429)
    assert g.state.total_calls > 0
    g.reset()
    assert g.state.total_calls == 0
    assert g.state.cooldown_until == 0.0
    assert g.can_call().allowed is True


# ── 23. snapshot ────────────────────────────────────────────────


def test_snapshot_has_expected_keys():
    g, _ = _make_guard(exchange="upbit", group="quotation")
    snap = g.snapshot()
    for k in ("exchange", "group", "disabled", "capacity",
              "refill_rate_per_sec", "safety_buffer",
              "remaining_tokens", "remaining_header", "used_weight",
              "cooldown_remaining_sec", "consecutive_failures",
              "last_error_code", "current_retry_attempt", "max_retries",
              "stats", "policy_notes"):
        assert k in snap
    # secret 류 키 부재
    flat = repr(snap).lower()
    for bad in ("api_key", "api_secret", "passphrase", "access_token"):
        assert bad not in flat


# ── 24. Registry get 자동 생성 ─────────────────────────────────


def test_registry_get_creates_default_guard():
    clk = _FakeClock()
    reg = ExchangeRateLimitRegistry(time_fn=clk.time, sleep_fn=clk.sleep)
    g = reg.get("upbit", "quotation")
    assert isinstance(g, RateLimitGuard)
    # 같은 키는 동일 인스턴스
    assert reg.get("upbit", "quotation") is g


def test_registry_unknown_falls_back_to_conservative():
    reg = ExchangeRateLimitRegistry()
    g = reg.get("alien", "weird")
    assert "default conservative" in g.policy.notes


# ── 25. register custom policy ─────────────────────────────────


def test_registry_register_custom_policy():
    reg = ExchangeRateLimitRegistry()
    custom = RateLimitPolicy(
        exchange="upbit", group="quotation",
        capacity=99, refill_rate_per_sec=99.0,
    )
    g = reg.register(custom)
    assert reg.get("upbit", "quotation") is g
    assert g.policy.capacity == 99


# ── 26. snapshot_all 정렬 + count ──────────────────────────────


def test_registry_snapshot_all_sorted():
    reg = build_default_registry()
    snap = reg.snapshot_all()
    assert snap["count"] >= 5
    pairs = [(g["exchange"], g["group"]) for g in snap["guards"]]
    assert pairs == sorted(pairs)


# ── 27. build_default_registry preload ─────────────────────────


def test_build_default_registry_preloads_all():
    reg = build_default_registry(preload=True)
    known = set(reg.known_pairs())
    expected = set(list_default_policies().keys())
    assert expected.issubset(known)


# ── 28. REST API ───────────────────────────────────────────────


@pytest.fixture
def api_client():
    from app.main import app
    from app.api.deps import get_rate_limit_registry as _dep

    reg = build_default_registry(preload=True)
    app.dependency_overrides[_dep] = lambda: reg
    yield TestClient(app), reg
    app.dependency_overrides.pop(_dep, None)


def test_api_rate_limits_returns_guards(api_client):
    client, reg = api_client
    r = client.get("/api/rate-limits")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == len(reg.known_pairs())
    assert any(g["exchange"] == "upbit" for g in body["guards"])
    assert "updated_at" in body
    assert "verify against exchange documentation" in body["warning"]


def test_api_rate_limits_no_secret_in_response(api_client):
    client, _ = api_client
    r = client.get("/api/rate-limits")
    flat = r.text.lower()
    for bad in ("api_key", "api_secret", "passphrase", "access_token",
                "x_mbx_apikey", "ok_access_sign"):
        assert bad not in flat


# ── 29-31. 정적 회귀 ───────────────────────────────────────────


_REPO_BACKEND_APP = Path(__file__).resolve().parent.parent / "app"


def test_rate_limit_guard_no_forbidden_strings():
    forbidden = (
        "ENABLE_LIVE_TRADING = True",
        "ENABLE_AI_EXECUTION = True",
        "ENABLE_CRYPTO_FUTURES_LIVE = True",
        "while True:",
        "requests.post(",
        "httpx.post(",
    )
    for fname in ("rate_limit_guard.py", "api_limits.py"):
        text = (_REPO_BACKEND_APP / "brokers" / fname).read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in text, f"{fname} contains {needle!r}"


def test_rate_limit_guard_no_network_sdk_imports():
    pat = re.compile(
        r"^\s*(?:import\s+(?:requests|httpx|ccxt|pyupbit|"
        r"binance|binance_connector|okx)|"
        r"from\s+(?:requests|httpx|ccxt|pyupbit|"
        r"binance|binance_connector|okx))",
        re.M,
    )
    for fname in ("rate_limit_guard.py", "api_limits.py"):
        text = (_REPO_BACKEND_APP / "brokers" / fname).read_text(encoding="utf-8")
        assert not pat.search(text), f"{fname} imports network library"


def test_brokers_exports_guard_symbols():
    from app import brokers
    for name in (
        "RateLimitPolicy",
        "AcquireDecision", "RetryDecision", "GuardState",
        "RateLimitGuard", "ExchangeRateLimitRegistry", "build_default_registry",
        "list_default_policies", "get_default_policy",
        "parse_retry_after",
        "ERROR_KIND_429", "ERROR_KIND_418", "ERROR_KIND_OKX_50011",
        "ERROR_KIND_AUTH", "ERROR_KIND_INVALID",
    ):
        assert name in brokers.__all__, f"{name} not exported"
        assert hasattr(brokers, name)


# ── 32. Strategy/Agent 직접 호출 부재 ──────────────────────────


def _scan(directory, pattern, glob="**/*.py"):
    hits = []
    for p in directory.glob(glob):
        if "__pycache__" in p.parts:
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        if pattern.search(text):
            hits.append(p)
    return hits


def test_strategies_do_not_import_rate_limit_guard():
    pat = re.compile(
        r"(?:from|import)\s+app\.brokers\.(?:rate_limit_guard|api_limits)",
    )
    hits = _scan(_REPO_BACKEND_APP / "strategies", pat)
    assert not hits, f"strategy imports rate-limit module: {hits}"


def test_agents_do_not_import_rate_limit_guard():
    pat = re.compile(
        r"(?:from|import)\s+app\.brokers\.(?:rate_limit_guard|api_limits)",
    )
    whitelist = {"compliance.py"}
    hits = [p for p in _scan(_REPO_BACKEND_APP / "agents", pat)
            if p.name not in whitelist]
    assert not hits, f"agent imports rate-limit module: {hits}"
