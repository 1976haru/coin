"""Strategies 라우터 — /api/strategies/*, /api/agents/catalog."""
from dataclasses import asdict
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.strategies.base import collect_default_strategies
from app.agents.base import collect_default_agents

from .deps import kimp_strategy, trend_strategy, agent

router = APIRouter()


@router.get("/api/strategies/catalog")
def strategies_catalog():
    """등록된 전략 목록과 capability 메타데이터 (#29)."""
    registry = collect_default_strategies()
    return {"strategies": registry.catalog(), "count": len(registry.names())}


@router.get("/api/agents/catalog")
def agents_catalog():
    """등록된 Agent 목록과 capability (#37). 공개 endpoint."""
    registry = collect_default_agents()
    return {"agents": registry.catalog(), "count": len(registry.names())}


class KimpRequest(BaseModel):
    symbol: str = "BTC"
    upbit_price_krw: float
    okx_price_usdt: float
    usdt_krw: float
    deposit_withdrawal_ok: bool = True
    fx_anomaly_ok: bool = True
    liquidity_ok: bool = True
    bull_market_block: bool = False
    upbit_spread_pct: float = 0.001
    okx_spread_pct: float = 0.001
    funding_rate_pct: float = 0.0


@router.post("/api/strategies/kimp/signal")
def kimp_signal(req: KimpRequest):
    sig = kimp_strategy.generate_signal(**req.model_dump())
    result = asdict(sig)
    agent_decision = agent.decide(result, {})
    return {"signal": result, "agent": asdict(agent_decision)}


@router.post("/api/strategies/trend/signal")
def trend_signal(body: dict):
    closes = body.get("closes", [])
    adx    = body.get("adx", 20.0)
    vol    = body.get("volume_ratio", 1.0)
    if not closes:
        raise HTTPException(400, "closes 배열 필요")
    sig = trend_strategy.generate(closes, adx=adx, volume_ratio=vol)
    return asdict(sig)
