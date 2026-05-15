# kim_bot/logic/exit_engine.py
from utils.vwap import vwap_to_fill

def expected_net_bps_for_close(side: str, entry_price: float, pos_qty: float,
                               okx_orderbook: dict, fees_bps_roundtrip: int,
                               slippage_bps: int) -> float:
    """ 청산 시 기대 순이익(bps)을 반환합니다. """
    if side == 'short':
        # 숏 포지션 청산(매수)은 매도 호가(asks)에서 체결됨
        expected_exit = vwap_to_fill(okx_orderbook['asks'], abs(pos_qty))
        gross = (entry_price - expected_exit) / entry_price * 1e4
    else: # 'long'
        # 롱 포지션 청산(매도)은 매수 호가(bids)에서 체결됨
        expected_exit = vwap_to_fill(okx_orderbook['bids'], abs(pos_qty))
        gross = (expected_exit - entry_price) / entry_price * 1e4

    # 총 수익률(bps)에서 왕복 수수료와 슬리피지 버퍼를 차감
    net = gross - (fees_bps_roundtrip + slippage_bps)
    return net

def should_take_profit(net_bps: float, cfg: dict) -> bool:
    # 기대 순익이 (목표수익률 + 안전마진) 이상일 때 익절
    return net_bps >= (cfg['pnl_calculation']['tp_target_bps'] + cfg['pnl_calculation']['safety_bps'])

def should_stop_loss(net_bps: float, cfg: dict) -> bool:
    # 기대 순익이 손절 기준점 이하일 때 손절 (대칭형 손절)
    return net_bps <= -cfg['pnl_calculation']['tp_target_bps']