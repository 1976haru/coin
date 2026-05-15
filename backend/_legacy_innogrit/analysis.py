import pandas as pd
from utils.logger import logger
import os
from datetime import datetime, timedelta
import asyncio

async def run_periodic_analysis(fill_logger, hours=12):
    """지정된 시간 동안의 거래를 분석하고 fill_logger에 요약 정보를 기록합니다."""
    await asyncio.sleep(2) # 다른 I/O 작업이 파일을 사용하는 것을 기다림
    input_filename = 'trade_log.csv'
    if not os.path.exists(input_filename):
        return

    try:
        df = pd.read_csv(input_filename)
        if df.empty:
            fill_logger.info(f"PERF SUMMARY ({hours}h) | No trades to analyze.")
            return
            
        df['timestamp'] = pd.to_datetime(df['timestamp'], format='mixed', errors='coerce')
        df.dropna(subset=['timestamp'], inplace=True)
        
        # 최근 N시간 데이터 필터링
        time_threshold = datetime.now() - timedelta(hours=hours)
        recent_trades = df[df['timestamp'] > time_threshold].copy()
        
        # action 컬럼에서 공백 제거
        recent_trades['action'] = recent_trades['action'].str.strip()

        exits = recent_trades[recent_trades['action'].str.contains('exit')].copy()
        
        if len(exits) == 0:
            fill_logger.info(f"PERF SUMMARY ({hours}h) | No trades to analyze in the last {hours} hours.")
            return

        entries = recent_trades[recent_trades['action'].str.contains('enter')]
        
        total_pnl = 0
        winning_trades = 0
        
        for _, exit_trade in exits.iterrows():
            # 같은 코인이고, 청산 시간보다 이전인 진입 기록 필터링
            possible_entries = entries[
                (entries['coin'] == exit_trade['coin']) & 
                (entries['timestamp'] < exit_trade['timestamp'])
            ]
            if not possible_entries.empty:
                # 가장 마지막 진입 기록을 매칭
                entry = possible_entries.sort_values(by='timestamp', ascending=False).iloc[0]
                
                pnl = 0
                # 김프 수익 계산
                if 'short' in exit_trade['action']:
                    pnl = entry['kimp_rate'] - exit_trade['kimp_rate']
                elif 'long' in exit_trade['action']:
                    pnl = exit_trade['kimp_rate'] - entry['kimp_rate']
                
                total_pnl += pnl
                if pnl > 0:
                    winning_trades += 1
        
        total_trades = len(exits)
        win_rate = (winning_trades / total_trades) * 100 if total_trades > 0 else 0
        avg_pnl = total_pnl / total_trades if total_trades > 0 else 0

        summary_msg = (
            f"PERF SUMMARY ({hours}h) | Trades: {total_trades}, "
            f"WinRate: {win_rate:.2f}%, TotalKimpPNL: {total_pnl:.2f}%, AvgKimpPNL: {avg_pnl:.2f}%"
        )
        fill_logger.info(summary_msg)

    except Exception as e:
        logger.error(f"주기적 분석 오류: {e}", exc_info=True)

def analyze_trades_from_file():
    """
    trade_log.csv 파일을 읽어 전체 거래 성과를 분석하고,
    결과를 trade_summary.csv 파일로 저장하며 콘솔에 출력합니다.
    """
    # (기존 analysis.py의 analyze_trades 함수 내용)
    pass

if __name__ == "__main__":
    analyze_trades_from_file()