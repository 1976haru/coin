import time
import asyncio
from datetime import datetime
import random
from utils.logger import logger
import json
import os

STATE_FILE = 'config/demo_state.json'

class DemoTradeManager:
    def __init__(self, config_manager, order_csv_logger, fill_csv_logger, balance_csv_logger):
        self.config_manager = config_manager
        self.order_csv_logger = order_csv_logger
        self.fill_csv_logger = fill_csv_logger
        self.balance_csv_logger = balance_csv_logger
        
        self._load_state()
        self.initial_balance = self.balance
        
        self.trade_count = 0
        self.winning_trades = 0
        self.last_entry_fee = 0  # ✨ [추가] 마지막 진입 수수료를 저장할 변수
        
        self._update_simulation_params()
        self._is_running = True
        self._background_tasks = []

    def _update_simulation_params(self):
        config = self.config_manager.get_config()
        fees_config = config.get('fees', {})
        sim_config = config.get('simulation', {})
        
        self.fee_rate = fees_config.get('okx_taker', 0.0005)
        self.slippage_rate = sim_config.get('slippage_rate', 0.0005)
        self.funding_rate = sim_config.get('funding_rate', 0.0001)
        self.fill_chance = sim_config.get('fill_chance', 0.95)
        self.simulation_fill_delay_sec = (
            sim_config.get('min_latency_ms', 50) / 1000,
            sim_config.get('max_latency_ms', 200) / 1000
        )

    def _load_state(self):
        config = self.config_manager.get_config()
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, 'r') as f:
                    state = json.load(f)
                self.balance = state.get('balance', config.get('demo_mode_balance', 1000))
                self.positions = state.get('positions', {})
                logger.info(f"저장된 데모 상태(demo_state.json)를 불러옵니다. 잔고: ${self.balance:,.4f}, 포지션: {list(self.positions.keys())}")
            except (json.JSONDecodeError, KeyError):
                self._initialize_default_state(config)
        else:
            self._initialize_default_state(config)

    def _initialize_default_state(self, config):
        self.balance = config.get('demo_mode_balance', 1000)
        self.positions = {}
        logger.info(f"기본 설정 잔고(config.json)로 데모 계좌를 초기화합니다: ${self.balance:,.4f}")

    def _save_state(self):
        try:
            with open(STATE_FILE, 'w') as f:
                state = {'balance': self.balance, 'positions': self.positions}
                json.dump(state, f, indent=4)
            logger.info(f"현재 데모 상태를 {STATE_FILE}에 저장했습니다.")
        except Exception as e:
            logger.error(f"데모 상태 저장 중 오류 발생: {e}", exc_info=True)

    async def initialize(self):
        self._background_tasks.append(asyncio.create_task(self._periodic_funding_fee_simulation()))
        logger.info("모의투자 매니저 초기화 완료. 펀딩비 시뮬레이션을 시작합니다.")

    async def _periodic_funding_fee_simulation(self):
        while self._is_running:
            await asyncio.sleep(8 * 60 * 60)
            if not self.positions: continue

            logger.info("펀딩비 시뮬레이션을 진행합니다...")
            funding_fee_applied = 0
            for coin, position in self.positions.items():
                position_value = position['amount'] * position['entry_price']
                funding_fee = position_value * self.funding_rate
                if position['type'] == 'short':
                    funding_fee = -funding_fee
                
                balance_before = self.balance
                self.balance -= funding_fee
                funding_fee_applied += funding_fee
                
                ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
                self.balance_csv_logger.info(f"{ts},FUNDING,{-funding_fee:.4f},{balance_before:.4f},{self.balance:.4f},{coin}")

            logger.info(f"펀딩비 시뮬레이션 완료. 총 {funding_fee_applied:.4f} USDT 반영. 현재 잔고: ${self.balance:.4f}")

    def _get_randomized_trade_amount(self):
        config = self.config_manager.get_config()
        base_amount = config.get('trade_amount_usdt', 150)
        leverage = config.get('leverage', 1)
        randomizer_config = config.get('size_randomizer', {})

        if not randomizer_config.get('enabled', False):
            return base_amount * leverage
        
        min_factor, max_factor = randomizer_config.get('min_factor', 0.9), randomizer_config.get('max_factor', 1.1)
        randomized_amount = random.triangular(base_amount * min_factor, base_amount * max_factor, base_amount)
        return randomized_amount * leverage

    async def get_balance(self):
        return {"totalEq": str(self.balance)}

    async def get_open_positions(self):
        return self.positions
    
    # ✨ [추가] PositionManager가 마지막 진입 수수료를 가져갈 수 있도록 함수 추가
    async def get_last_entry_fee(self, coin):
        return self.last_entry_fee

    def _simulate_slippage(self, price, direction):
        slippage = price * self.slippage_rate * random.uniform(0.8, 1.2) * direction
        return price + slippage

    async def _simulate_limit_order(self, coin, side, amount, price):
        order_id = f"demo_{int(time.time() * 1000)}"
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        
        log_side = "BUY" if "LONG" in side else "SELL"
        self.order_csv_logger.info(f"{ts},{side},{coin},LIMIT,{log_side},{amount:.8f},{price:.8f},{order_id},placed,")

        await asyncio.sleep(random.uniform(*self.simulation_fill_delay_sec))
        
        if random.random() > self.fill_chance:
            logger.warning(f"[{coin}] (DEMO) 주문 ID {order_id}가 체결되지 않고 타임아웃되었습니다 (미체결 시뮬레이션).")
            self.order_csv_logger.info(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]},{side},{coin},LIMIT,{log_side},{amount:.8f},{price:.8f},{order_id},cancelled,timeout")
            return None, "Order timeout (simulated)"
            
        logger.info(f"[{coin}] (DEMO) 주문 ID {order_id}가 성공적으로 체결되었습니다.")
        
        direction = 1 if "SHORT" in side else -1
        executed_price = self._simulate_slippage(price, direction)
        fee = (amount * executed_price) * self.fee_rate
        
        return {'id': order_id, 'price': executed_price, 'amount': amount, 'fee': fee}, None

    async def enter_long_position(self, coin, price):
        amount_usdt = self._get_randomized_trade_amount()
        amount_coin = amount_usdt / price
        
        order_result, error_msg = await self._simulate_limit_order(coin, 'ENTER-LONG', amount_coin, price)
        if error_msg: return None, error_msg

        self.last_entry_fee = order_result['fee']  # ✨ [수정] 진입 수수료 저장
        balance_before = self.balance
        self.balance -= self.last_entry_fee
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        self.balance_csv_logger.info(f"{ts},FEE,{-self.last_entry_fee:.4f},{balance_before:.4f},{self.balance:.4f},ENTER-LONG-{coin}")

        self.positions[coin] = {'amount': order_result['amount'], 'entry_price': order_result['price'], 'type': 'long', 'entry_time': time.time(), 'entry_fee': self.last_entry_fee}
        self.trade_count += 1
        return {'id': order_result['id'], 'amount': order_result['amount'], 'price': order_result['price']}, None

    async def enter_short_position(self, coin, price):
        amount_usdt = self._get_randomized_trade_amount()
        amount_coin = amount_usdt / price

        order_result, error_msg = await self._simulate_limit_order(coin, 'ENTER-SHORT', amount_coin, price)
        if error_msg: return None, error_msg

        self.last_entry_fee = order_result['fee']  # ✨ [수정] 진입 수수료 저장
        balance_before = self.balance
        self.balance -= self.last_entry_fee
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        self.balance_csv_logger.info(f"{ts},FEE,{-self.last_entry_fee:.4f},{balance_before:.4f},{self.balance:.4f},ENTER-SHORT-{coin}")

        self.positions[coin] = {'amount': order_result['amount'], 'entry_price': order_result['price'], 'type': 'short', 'entry_time': time.time(), 'entry_fee': self.last_entry_fee}
        self.trade_count += 1
        return {'id': order_result['id'], 'amount': order_result['amount'], 'price': order_result['price']}, None

    async def close_long_position(self, coin, price, amount):
        if coin not in self.positions: return None, "Position not found"
        
        order_result, error_msg = await self._simulate_limit_order(coin, 'CLOSE-LONG', amount, price)
        if error_msg: return None, error_msg

        position_info = self.positions[coin]
        gross_pnl = (order_result['price'] - position_info['entry_price']) * amount
        net_pnl = gross_pnl - position_info.get('entry_fee', 0) - order_result['fee']
        
        if net_pnl > 0: self.winning_trades += 1
        
        balance_before = self.balance
        self.balance += net_pnl
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        self.balance_csv_logger.info(f"{ts},PNL,{net_pnl:+.4f},{balance_before:.4f},{self.balance:.4f},CLOSE-LONG-{coin}")
        
        logger.info(f"[{coin}] (DEMO) 롱 포지션 청산. PNL: ${net_pnl:.4f}, 현재 잔고: ${self.balance:.4f}")
        del self.positions[coin]
        return {'id': order_result['id'], 'amount': amount, 'price': order_result['price'], 'net_pnl': net_pnl, 'exit_fee': order_result['fee']}, None

    async def close_short_position(self, coin, price, amount):
        if coin not in self.positions: return None, "Position not found"
            
        order_result, error_msg = await self._simulate_limit_order(coin, 'CLOSE-SHORT', amount, price)
        if error_msg: return None, error_msg
            
        position_info = self.positions[coin]
        gross_pnl = (position_info['entry_price'] - order_result['price']) * amount
        net_pnl = gross_pnl - position_info.get('entry_fee', 0) - order_result['fee']

        if net_pnl > 0: self.winning_trades += 1
        
        balance_before = self.balance
        self.balance += net_pnl
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        self.balance_csv_logger.info(f"{ts},PNL,{net_pnl:+.4f},{balance_before:.4f},{self.balance:.4f},CLOSE-SHORT-{coin}")

        logger.info(f"[{coin}] (DEMO) 숏 포지션 청산. PNL: ${net_pnl:.4f}, 현재 잔고: ${self.balance:.4f}")
        del self.positions[coin]
        return {'id': order_result['id'], 'amount': amount, 'price': order_result['price'], 'net_pnl': net_pnl, 'exit_fee': order_result['fee']}, None

    async def close_all_positions(self):
        positions_to_close = list(self.positions.keys())
        for coin in positions_to_close:
            position_data = self.positions[coin]
            mock_close_price = position_data['entry_price'] * random.uniform(0.99, 1.01)
            
            if position_data['type'] == 'long':
                await self.close_long_position(coin, mock_close_price, position_data['amount'])
            else:
                await self.close_short_position(coin, mock_close_price, position_data['amount'])
        self._save_state()

    def display_final_stats(self):
        logger.info("--- 데모 모드 최종 통계 ---")
        logger.info(f"총 거래 횟수: {self.trade_count}회")
        if self.trade_count > 0:
            win_rate = (self.winning_trades / self.trade_count) * 100
            logger.info(f"승리한 거래: {self.winning_trades}회")
            logger.info(f"승률: {win_rate:.2f}%")
        else:
            logger.info("승률: 거래 기록 없음")
        
        final_net_pnl = self.balance - self.initial_balance
        logger.info("-" * 20)
        logger.info(f"초기 잔고: ${self.initial_balance:.4f}")
        logger.info(f"최종 순수익 (Net PNL): ${final_net_pnl:.4f}")
        logger.info(f"최종 잔고: ${self.balance:.4f}")

    async def close(self):
        self._is_running = False
        for task in self._background_tasks:
            task.cancel()
        await asyncio.gather(*self._background_tasks, return_exceptions=True)
        self._save_state()
        logger.info("데모 거래 관리자 종료. 최종 상태를 저장했습니다.")