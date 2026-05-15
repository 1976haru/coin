# kim_bot/logic/quotes_guard.py
import time

def quotes_fresh(upbit_ts_ms: int, okx_ts_ms: int, okx_tick_changed: bool,
                 sync_window_ms: int, okx_stale_ms: int) -> bool:
    now_ms = int(time.time() * 1000)
    # 두 거래소 시세 타임스탬프 차이가 sync_window_ms 이내인가?
    sync_ok = abs(upbit_ts_ms - okx_ts_ms) <= sync_window_ms
    # OKX 시세가 okx_stale_ms 이내에 갱신되었는가?
    stale_okx = (now_ms - okx_ts_ms) <= okx_stale_ms
    # 모든 조건을 만족하고, OKX 틱이 변경되었을 때만 True 반환
    return sync_ok and stale_okx and okx_tick_changed