# kim_bot/utils/vwap.py
def vwap_to_fill(levels, qty):
    """
    오더북(levels)과 체결 수량(qty)을 받아 VWAP를 계산합니다.
    levels: [[price, size], ...], qty: 체결하고자 하는 수량(양수)
    """
    remain = qty
    cost = 0.0
    for price, size in levels:
        take = min(remain, size)
        cost += take * price
        remain -= take
        if remain <= 1e-12:  # 부동소수점 오차 감안
            break
    if qty <= 0 or remain > 1e-12:
        raise ValueError("오더북 유동성 부족 또는 qty=0")
    return cost / qty