import logging
import logging.handlers
import sys
import os
import atexit
from datetime import datetime

# --- 범용 .log 파일 로거 (디버깅 및 일반 정보용) ---
def setup_log_logger(name, log_file, level=logging.INFO, formatter_str='%(asctime)s.%(msecs)03d - %(name)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S'):
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(level)
    logger.propagate = False
    
    log_dir = os.path.dirname(log_file)
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir)
        
    handler = logging.handlers.WatchedFileHandler(log_file, encoding='utf-8')
    formatter = logging.Formatter(formatter_str, datefmt=datefmt)
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    return logger

# --- [수정] 안정적인 CSV 로거 생성 함수 ---
def make_csv_logger(name: str, filepath: str) -> logging.Logger:
    """
    안정성을 보강한 CSV 로거 생성 함수.
    파일 핸들러 부착, 독립성, 레벨 설정을 보장합니다.
    """
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    logger = logging.getLogger(name)
    
    # 핸들러 중복 추가 방지
    if any(isinstance(h, logging.FileHandler) and h.baseFilename == os.path.abspath(filepath) for h in logger.handlers):
        return logger

    logger.setLevel(logging.INFO)
    logger.propagate = False # 다른 로거로 전파 방지

    # 파일 핸들러 설정
    fh = logging.FileHandler(filepath, encoding='utf-8-sig')
    fmt = logging.Formatter('%(message)s')
    fh.setFormatter(fmt)
    logger.addHandler(fh)
        
    return logger

# --- [추가] 프로그램 종료 시 모든 로그 버퍼를 flush하는 함수 ---
def flush_all_loggers():
    """atexit에 등록하여 프로그램 종료 시 모든 핸들러의 버퍼를 flush합니다."""
    for logger_instance in logging.Logger.manager.loggerDict.values():
        if isinstance(logger_instance, logging.Logger):
            for handler in logger_instance.handlers:
                try:
                    handler.flush()
                except Exception:
                    pass # 종료 시점의 오류는 무시

atexit.register(flush_all_loggers)


# --- 로거 인스턴스 생성 ---
# 범용 로거
logger = setup_log_logger('kim_bot', 'logs/trade_bot.log')

# ✅ [추가] 거래 사이클 로그 헤더 정의
TRADE_CYCLE_LOG_HEADER = (
    "trade_id,status,symbol,side,entry_reason,exit_reason,"
    "ts_entry_signal,ts_entry_filled,ts_exit_signal,ts_exit_filled,"
    "entry_price_signal,entry_price_avg,exit_price_signal,exit_price_avg,"
    "qty,fee_entry,fee_exit,pnl_net,pnl_pct,"
    "kimp_entry_signal,kimp_exit_signal,kimp_volatility_entry,"
    "strategy_params"
)

# CSV 로거 생성 (make_csv_logger 사용)
signal_csv_logger = make_csv_logger('signal_csv', 'logs/signal.csv')
order_csv_logger = make_csv_logger('order_csv', 'logs/order.csv')
fill_csv_logger = make_csv_logger('fill_csv', 'logs/fill.csv')
balance_csv_logger = make_csv_logger('balance_csv', 'logs/balance.csv')
trade_result_csv_logger = make_csv_logger('trade_result_csv', 'logs/trade_result.csv')

# ✅ [추가] 거래 사이클 로거 생성
trades_cycle_csv_logger = make_csv_logger('trades_cycle_csv', 'logs/trades_cycle.csv')