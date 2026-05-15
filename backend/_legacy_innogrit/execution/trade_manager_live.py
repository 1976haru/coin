import ccxt.async_support as ccxt_async
from utils.logger import logger
from datetime import datetime
import time
import asyncio
import os
import random

from utils.csv_loggers import CsvLogger
from logic.quotes_guard import quotes_fresh

class LiveTradeManager:
    """실제 OKX 거래소와 통신하여 거래를 실행하고, 슬리피지를 제어하는 청산 로직을 포함하는 클래스"""
    def __init__(self, config_manager, order_csv_logger=None, fill_csv_logger=None, balance_csv_logger=None):
        self.config_manager = config_manager
        self.config = self.config_manager.get_config()

        self._order_header = ["ts", "action", "coin", "order_type", "side", "qty", "price", "order_id", "status", "extra"]
        self._fill_header = ["ts", "action", "coin", "price", "qty", "fee", "net_pnl"]
        self._balance_header = ["ts", "type", "delta", "before", "after", "details"]
        self._trade_log_header = ["ts","symbol","side","entry_price","avg_exit","pnl","reason","status","order_id"]

        self.order_log = CsvLogger(self.config, "order.csv", self._order_header)
        self.fill_log = CsvLogger(self.config, "fill.csv", self._fill_header)
        self.bal_log = CsvLogger(self.config, "balance.csv", self._balance_header)
        self.trade_log = CsvLogger(self.config, "trade_log.csv", self._trade_log_header)

        self.positions = {}
        self.okx = None
        self.api_key = os.getenv('OKX_API_KEY')
        self.api_secret = os.getenv('OKX_API_SECRET')
        self.api_password = os.getenv('OKX_API_PASSWORD')
        self.initial_balance = 0
        self.balance = 0
        self.trade_count = 0
        self.winning_trades = 0
        self.last_entry_fee = 0

        trade_rules = self.config.get('trading_rules', {})
        self.order_timeout_sec = trade_rules.get('order_timeout_sec', 8)
        self.cancel_backoff = trade_rules.get('cancel_backoff', [0.2, 0.5, 1.0])
        self.close_order_fetch_timeout_sec = trade_rules.get('close_order_fetch_timeout_sec', 10)

        self.last_funding_sync_time = time.time() - 3600 * 24
        self.processed_bill_ids = set()
        self._is_running = True
        self._background_tasks = []

    def _get_randomized_trade_amount(self):
        base_amount = self.config.get('trade_amount_usdt', 150)
        leverage = self.config.get('leverage', 1)
        randomizer_config = self.config.get('size_randomizer', {})
        
        if not randomizer_config.get('enabled', False):
            return base_amount * leverage

        min_factor, max_factor = randomizer_config.get('min_factor', 0.9), randomizer_config.get('max_factor', 1.1)
        randomized_amount = random.triangular(base_amount * min_factor, base_amount * max_factor, base_amount)
        return randomized_amount * leverage

    def _usdt_to_contracts(self, symbol, amount_usdt, price):
        try:
            market = self.okx.market(symbol)
            adjusted_price = float(self.okx.price_to_precision(symbol, price))
            if adjusted_price == 0:
                logger.error(f"[{symbol.split('/')[0]}] 가격이 0으로 조정되어 계약 수를 계산할 수 없습니다.")
                return None

            contract_size = float(market.get('contractSize', 1) or 1)
            contracts = amount_usdt / (adjusted_price * contract_size)
            
            return float(self.okx.amount_to_precision(symbol, contracts))
        except Exception as e:
            logger.error(f"[{symbol.split('/')[0]}] 계약 수 변환 중 오류: {e}")
            return None

    async def initialize(self):
        if not all([self.api_key, self.api_secret, self.api_password]):
            raise ValueError("OKX API credentials are not set.")
        try:
            self.okx = ccxt_async.okx({'apiKey': self.api_key, 'secret': self.api_secret, 'password': self.api_password, 'options': {'defaultType': 'swap'}})
            await self.okx.load_markets()
            balance_info = await self.okx.fetch_balance()
            self.balance = float(balance_info.get('total', {}).get('USDT', 0))
            self.initial_balance = self.balance

            current_positions = await self.get_open_positions()
            if current_positions:
                self.positions = current_positions
                logger.warning(f"동기화 완료. 거래소에 {len(self.positions)}개의 포지션이 존재합니다: {list(self.positions.keys())}")
            else:
                logger.info("동기화 완료. 현재 거래소에 열린 포지션이 없습니다.")

            logger.info(f"✅ 실제 매매(Live) 거래 관리자 초기화 완료. 초기 잔고: ${self.initial_balance:,.4f}")
            self._background_tasks.append(asyncio.create_task(self._periodic_funding_sync()))
        except Exception as e:
            logger.error(f"OKX 클라이언트 초기화 중 오류 발생: {e}", exc_info=True)
            if self.okx: await self.okx.close()
            raise

    async def _periodic_funding_sync(self):
        await asyncio.sleep(10)
        while self._is_running:
            try:
                logger.info("펀딩비 내역 동기화를 시작합니다...")
                funding_bills = await self.okx.fetch_funding_history(limit=100)
                if not funding_bills:
                    logger.info("최근 펀딩비 내역이 없습니다."); await asyncio.sleep(3600); continue
                new_funding_fee, new_bills_count = 0, 0
                for bill in sorted(funding_bills, key=lambda x: x['timestamp']):
                    bill_id = bill['id']
                    if bill_id not in self.processed_bill_ids:
                        amount = float(bill['amount']); new_funding_fee += amount; new_bills_count += 1
                        balance_before = self.balance; self.balance += amount
                        ts = datetime.fromtimestamp(bill['timestamp'] / 1000).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
                        coin = bill['symbol'].replace('/USDT:USDT', '')
                        self.bal_log.write({"ts": ts, "type": "FUNDING", "delta": f"{amount:+.4f}", "before": f"{balance_before:.4f}", "after": f"{self.balance:.4f}", "details": coin})
                        self.processed_bill_ids.add(bill_id)
                if new_bills_count > 0:
                    logger.info(f"펀딩비 동기화 완료. {new_bills_count}건, 총 {new_funding_fee:+.4f} USDT 반영. 현재 잔고: ${self.balance:.4f}")
                else:
                    logger.info("새로운 펀딩비 내역이 없습니다.")
                self.last_funding_sync_time = time.time()
            except Exception as e:
                logger.error(f"펀딩비 동기화 중 오류 발생: {e}", exc_info=True)
            await asyncio.sleep(3600)

    async def get_balance(self):
        try:
            balance_info = await self.okx.fetch_balance()
            self.balance = float(balance_info.get('total', {}).get('USDT', self.balance))
            return {"totalEq": str(self.balance)}
        except Exception as e:
            logger.error(f"실거래 잔고 조회 중 오류 발생: {e}")
            return {"totalEq": str(self.balance)}

    async def get_open_positions(self):
        try:
            open_positions = await self.okx.fetch_positions()
            active_positions = [p for p in open_positions if p.get('contracts') and float(p['contracts']) > 0]
            if not active_positions: return {}
            formatted_positions = {}
            for position in active_positions:
                info = position.get('info', {})
                symbol = info.get('instId', '').split('-')[0]
                pos_side = info.get('posSide', 'net')
                position_type = 'long' if pos_side == 'long' else 'short'
                amount = float(info.get('pos', 0))
                entry_price = float(info.get('avgPx', 0))
                if amount > 0:
                    formatted_positions[symbol] = {'type': position_type, 'amount': amount, 'entry_price': entry_price}
            return formatted_positions
        except Exception as e:
            logger.error(f"거래소에서 포지션 정보를 가져오는 중 오류 발생: {e}", exc_info=True)
            return {}
            
    async def get_last_entry_fee(self, coin):
        return self.last_entry_fee

    async def _cancel_order_with_retry(self, order_id, symbol, log_payload):
        for delay in self.cancel_backoff:
            try:
                await self.okx.cancel_order(order_id, symbol)
                logger.info(f"주문 ID {order_id} 취소 요청 성공.")
                return True
            except Exception as e:
                logger.warning(f"주문 {order_id} 취소 실패, {delay}초 후 재시도: {e}")
                await asyncio.sleep(delay)
        logger.error(f"최종적으로 주문 {order_id} 취소에 실패했습니다.")
        return False

# trade_manager_live.py 파일에서 이 함수를 찾아 교체하세요.

    async def _place_limit_order_with_timeout(self, coin, side, amount, price, position_side):
        symbol = f"{coin}/USDT:USDT"; params = {'posSide': position_side}; order = None
        log_action = f"ENTER-{position_side.upper()}"; order_side = 'buy' if 'buy' in side else 'sell'
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        
        try:
            market = self.okx.markets.get(symbol)
            if not market:
                reason = "Market data not available"
                logger.error(f"[{coin}] 마켓 정보를 찾을 수 없습니다.")
                self.order_log.write({"ts": ts, "action": log_action, "coin": coin, "status": "failed", "extra": reason})
                return None, reason
            
            adjusted_amount_str = self.okx.amount_to_precision(symbol, amount)
            adjusted_price_str = self.okx.price_to_precision(symbol, price)
            
            adjusted_amount = float(adjusted_amount_str)
            adjusted_price = float(adjusted_price_str)
            
            logger.info(f"[{coin}] 주문 값 조정: 수량({amount:.8f} -> {adjusted_amount_str}), 가격({price:.8f} -> {adjusted_price_str})")
            
        except Exception as e:
            msg = str(e).replace(',', ';')[:200]
            self.order_log.write({"ts": ts, "action": log_action, "coin": coin, "status": "precision_error", "extra": msg})
            logger.error(f"[{coin}] 주문 정밀도 변환 중 오류: {e}")
            return None, msg

        self.order_log.write({"ts": ts, "action": log_action, "coin": coin, "order_type": "LIMIT", "side": order_side, "qty": adjusted_amount_str, "price": adjusted_price_str, "status": "request"})

        try:
            # --- 코드 수정 시작 ---
            limits = market.get('limits', {})
            amount_limits = limits.get('amount', {})
            cost_limits = limits.get('cost', {})

            min_amount = amount_limits.get('min')
            if min_amount is not None and adjusted_amount < min_amount:
                reason = f"주문 수량({adjusted_amount_str})이 최소 주문 수량({min_amount})보다 작습니다."
                logger.warning(f"[{coin}] {reason} 주문을 보내지 않습니다.")
                self.order_log.write({"ts": datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3], "action": log_action, "coin": coin, "status": "failed", "extra": reason})
                return None, reason
            
            contract_size = float(market.get('contractSize', 1) or 1)
            order_cost = adjusted_amount * adjusted_price * contract_size
            min_cost = cost_limits.get('min', 0)
            if min_cost is not None and order_cost < min_cost:
                reason = f"주문 금액(${order_cost:.4f})이 최소 주문 금액(${min_cost})보다 작습니다."
                logger.warning(f"[{coin}] {reason} 주문을 보내지 않습니다.")
                self.order_log.write({"ts": datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3], "action": log_action, "coin": coin, "status": "failed", "extra": reason})
                return None, reason
            # --- 코드 수정 끝 ---
        except Exception as e:
            logger.error(f"[{coin}] 최소 주문 수량/금액 확인 중 오류: {e}")
            return None, str(e)

        order_id = None
        try:
            if order_side == 'buy':
                order = await self.okx.create_limit_buy_order(symbol, adjusted_amount, adjusted_price, params)
            else:
                order = await self.okx.create_limit_sell_order(symbol, adjusted_amount, adjusted_price, params)
            
            order_id = order['id']
            self.order_log.write({"ts": datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3], "action": log_action, "coin": coin, "order_id": order_id, "status": "placed"})
            
            start_time = time.time()
            while time.time() - start_time < self.order_timeout_sec:
                order_status = await self.okx.fetch_order(order_id, symbol)
                if order_status['status'] == 'closed':
                    logger.info(f"주문 ID {order_id}가 성공적으로 체결되었습니다.")
                    return ({'order': order_status, 'id': order_id}, None) if order_status.get('filled', 0) > 0 else (None, "Order closed without fill")
                await asyncio.sleep(0.5)

            logger.warning(f"주문 ID {order_id}가 타임아웃({self.order_timeout_sec}초)되어 취소를 시도합니다.")
            log_payload = {'action': log_action, 'coin': coin, 'side': order_side, 'qty': adjusted_amount_str, 'price': adjusted_price_str}
            
            await self._cancel_order_with_retry(order_id, symbol, log_payload)
            
            logger.info(f"주문 ID {order_id}의 최종 상태를 확인하여 경쟁 상태(Race Condition)를 방지합니다.")
            final_order_status = await self.okx.fetch_order(order_id, symbol)
            
            filled_amount = final_order_status.get('filled', 0)
            if filled_amount > 0:
                logger.warning(f"[경쟁 상태 감지/처리] 주문 {order_id}가 취소 전 {filled_amount} 만큼 체결되었습니다. 부분 체결로 처리합니다.")
                return {'order': final_order_status, 'id': order_id}, None
            else:
                logger.info(f"주문 ID {order_id}는 체결되지 않고 안전하게 취소되었습니다.")
                self.order_log.write({"ts": datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3], "order_id": order_id, "status": "cancelled", "extra": "timeout"})
                return None, "Order timeout"

        except Exception as e:
            error_message = str(e).replace('\n', ' ').replace(',', ';')
            logger.error(f"[{coin}] 지정가 주문 처리 중 오류 발생: {error_message}", exc_info=False)
            self.order_log.write({"ts": datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3], "status": "failed", "extra": error_message})
            if order_id: 
                log_payload = {'action': log_action, 'coin': coin, 'side': order_side, 'qty': adjusted_amount_str, 'price': adjusted_price_str}
                await self._cancel_order_with_retry(order_id, symbol, log_payload)
            return None, error_message
    async def enter_short_position(self, coin, price):
        symbol = f"{coin}/USDT:USDT"
        amount_usdt = self._get_randomized_trade_amount()
        contracts = self._usdt_to_contracts(symbol, amount_usdt, price)
        if contracts is None or contracts == 0:
            return None, "계약 수 변환 실패 또는 0"
            
        market = self.okx.market(symbol)
        contract_size = float(market.get('contractSize', 1) or 1)
        predicted_notional = contracts * price * contract_size
        max_notional = self.config.get('trading_rules', {}).get('max_notional_usdt', 50)
        
        if predicted_notional > max_notional:
            reason = f"예상 주문 금액(${predicted_notional:,.2f})이 최대 설정값(${max_notional:,.2f})을 초과합니다."
            logger.error(f"[{coin}] (Short) {reason} 주문을 취소합니다.")
            return None, reason

        trade_result, error_msg = await self._place_limit_order_with_timeout(coin, 'sell_short', contracts, price, 'short')
        
        if trade_result:
            order_status = trade_result['order']
            fee_info = order_status.get('fee'); entry_fee = float(fee_info.get('cost', 0)) if fee_info else 0
            self.last_entry_fee = entry_fee
            fill_price, fill_qty = order_status.get('average', price), order_status.get('filled')
            
            if not fill_qty or fill_qty <= 0:
                logger.warning(f"[{coin}] 주문 결과는 성공이나 체결 수량이 0입니다. 진입하지 않습니다.")
                return None, "Filled quantity is zero."
            
            self.positions[coin] = {'amount': fill_qty, 'entry_price': fill_price, 'type': 'short', 'entry_time': time.time(), 'entry_fee': entry_fee}
            self.trade_count += 1
            return {'id': trade_result['id'], 'amount': fill_qty, 'price': fill_price}, None
        return None, error_msg

    async def enter_long_position(self, coin, price):
        symbol = f"{coin}/USDT:USDT"
        amount_usdt = self._get_randomized_trade_amount()
        contracts = self._usdt_to_contracts(symbol, amount_usdt, price)
        if contracts is None or contracts == 0:
            return None, "계약 수 변환 실패 또는 0"

        market = self.okx.market(symbol)
        contract_size = float(market.get('contractSize', 1) or 1)
        predicted_notional = contracts * price * contract_size
        max_notional = self.config.get('trading_rules', {}).get('max_notional_usdt', 50)

        if predicted_notional > max_notional:
            reason = f"예상 주문 금액(${predicted_notional:,.2f})이 최대 설정값(${max_notional:,.2f})을 초과합니다."
            logger.error(f"[{coin}] (Long) {reason} 주문을 취소합니다.")
            return None, reason

        trade_result, error_msg = await self._place_limit_order_with_timeout(coin, 'buy_long', contracts, price, 'long')
        if trade_result:
            order_status = trade_result['order']
            fee_info = order_status.get('fee'); entry_fee = float(fee_info.get('cost', 0)) if fee_info else 0
            self.last_entry_fee = entry_fee
            fill_price, fill_qty = order_status.get('average', price), order_status.get('filled')
            
            if not fill_qty or fill_qty <= 0:
                logger.warning(f"[{coin}] 주문 결과는 성공이나 체결 수량이 0입니다. 진입하지 않습니다.")
                return None, "Filled quantity is zero."

            self.positions[coin] = {'amount': fill_qty, 'entry_price': fill_price, 'type': 'long', 'entry_time': time.time(), 'entry_fee': entry_fee}
            self.trade_count += 1
            return {'id': trade_result['id'], 'amount': fill_qty, 'price': fill_price}, None
        return None, error_msg
    
    async def _fetch_closed_order_with_retry(self, order_id, symbol):
        start_time = time.time()
        while time.time() - start_time < self.close_order_fetch_timeout_sec:
            try:
                order = await self.okx.fetch_order(order_id, symbol)
                if order['status'] == 'closed':
                    logger.info(f"주문 ID {order_id}의 최종 'closed' 상태를 확인했습니다.")
                    return order
            except Exception as e:
                logger.warning(f"최종 주문 상태 확인 중 오류 발생 (ID: {order_id}): {e}")
            await asyncio.sleep(0.5)
        logger.error(f"타임아웃({self.close_order_fetch_timeout_sec}초) 내에 주문 ID {order_id}의 최종 상태를 확인하지 못했습니다.")
        return None

    # <<<--- 여기가 수정된 부분입니다 --- START
    async def _execute_chase_limit_order(self, coin, side, amount, position_side):
        strategy_cfg = self.config.get('exit_strategy', {})
        symbol = f"{coin}/USDT:USDT"
        
        # 최초 호가 기록
        initial_chase_price = None
        try:
            orderbook = await self.okx.fetch_order_book(symbol)
            if side == 'buy' and orderbook.get('asks'):
                initial_chase_price = orderbook['asks'][0][0]
            elif side == 'sell' and orderbook.get('bids'):
                initial_chase_price = orderbook['bids'][0][0]
        except Exception as e:
            logger.warning(f"[{coin}] 지정가 추격 시작을 위한 초기 호가 조회 실패: {e}")

        for i in range(strategy_cfg.get('chase_retries', 3)):
            try:
                # 매 시도마다 최신 호가 사용
                orderbook = await self.okx.fetch_order_book(symbol)
                
                offset = strategy_cfg.get('price_offset_bps', 2) / 10000
                if side == 'buy':
                    best_price = orderbook['asks'][0][0] if orderbook.get('asks') else None
                    if not best_price: raise ValueError("매도 호가 없음")
                    limit_price = best_price * (1 + offset)
                else: # side == 'sell'
                    best_price = orderbook['bids'][0][0] if orderbook.get('bids') else None
                    if not best_price: raise ValueError("매수 호가 없음")
                    limit_price = best_price * (1 - offset)
                
                # 안전장치: 최대 허용 슬리피지 검사
                if initial_chase_price:
                    max_slippage_pct = strategy_cfg.get('chase_max_slippage_pct', 1.0)
                    slippage_pct = (abs(limit_price - initial_chase_price) / initial_chase_price) * 100
                    if slippage_pct > max_slippage_pct:
                        logger.warning(f"[{coin}] 지정가 추격 중 최대 허용 슬리피지({max_slippage_pct}%)를 초과했습니다 (현재 슬리피지: {slippage_pct:.2f}%). 시장가로 전환합니다.")
                        break # for 루프를 탈출하여 시장가 주문 실행

                logger.info(f"[{coin}] 지정가 추격 시도 ({i+1}/{strategy_cfg.get('chase_retries', 3)}): 목표가 {limit_price:.8f}")
                
                original_timeout = self.order_timeout_sec
                self.order_timeout_sec = strategy_cfg.get('chase_time_limit_ms', 250) / 1000
                
                action_side = f"{'buy' if side == 'buy' else 'sell'}_{position_side}"
                result, error = await self._place_limit_order_with_timeout(coin, action_side, amount, limit_price, position_side)
                
                self.order_timeout_sec = original_timeout

                if result and result.get('order'):
                    return result['order'], None

                logger.warning(f"[{coin}] 지정가 주문이 시간 내 체결되지 않았습니다. 재시도합니다.")

            except Exception as e:
                logger.error(f"[{coin}] 지정가 추격 중 오류 발생: {e}", exc_info=True)
                await asyncio.sleep(0.1)
        
        # 지정가 추격이 모두 실패했거나, 슬리피지 제한을 초과했을 경우 시장가로 최종 청산
        logger.warning(f"[{coin}] 지정가 추격이 모두 실패했거나 슬리피지 제한을 초과하여, 시장가로 최종 청산을 시도합니다.")
        params = {'posSide': position_side, 'reduceOnly': True}
        if side == 'buy':
            return await self.okx.create_market_buy_order(symbol, amount, params), None
        else:
            return await self.okx.create_market_sell_order(symbol, amount, params), None
    # <<<--- 여기가 수정된 부분입니다 --- END

    async def close_short_position(self, coin, price, amount):
        position_info = self.positions.get(coin)
        if not position_info or not position_info.get('entry_price'):
            return None, "유효한 포지션 정보 없음"
        
        exit_strategy = self.config.get('exit_strategy', {})
        
        try:
            if exit_strategy.get('use_limit_order_chase', False):
                logger.info(f"[{coin}] 지정가 추격(Chase)으로 숏 포지션 청산을 시도합니다.")
                initial_order_status, err = await self._execute_chase_limit_order(coin, 'buy', amount, 'short')
                if err: raise Exception(err)
            else:
                logger.info(f"[{coin}] 시장가 주문으로 숏 포지션 청산을 시도합니다.")
                params = {'posSide': 'short', 'reduceOnly': True}
                initial_order_status = await self.okx.create_market_buy_order(f"{coin}/USDT:USDT", amount, params)

            if not initial_order_status or not initial_order_status.get('id'):
                return None, "거래소로부터 유효한 주문 응답을 받지 못했습니다."

            logger.info(f"[{coin}] 청산 주문(ID: {initial_order_status['id']}) 제출 완료. 최종 상태 확인 중...")
            final_order_status = await self._fetch_closed_order_with_retry(initial_order_status['id'], f"{coin}/USDT:USDT")

            if not final_order_status:
                return None, "최종 체결 상태를 확인하지 못했습니다."

            entry_price = position_info['entry_price']
            exit_price = final_order_status.get('average') 
            if not exit_price or exit_price <= 0:
                return None, "유효한 평균 체결가를 얻지 못했습니다."

            fee_info = final_order_status.get('fee', {})
            exit_fee = float(fee_info.get('cost', 0)) if fee_info else 0
            filled_amount = final_order_status.get('filled', amount)
            gross_pnl = (entry_price - exit_price) * filled_amount
            net_pnl = gross_pnl - position_info.get('entry_fee', 0) - exit_fee
            
            if net_pnl > 0: self.winning_trades += 1

            logger.info(f"[{coin}] (LIVE) 숏 포지션 청산 완료. PNL: ${net_pnl:.4f}")
            if coin in self.positions: del self.positions[coin]
            
            return {
                'id': final_order_status['id'], 
                'amount': filled_amount, 
                'price': exit_price, 
                'net_pnl': net_pnl,
                'gross_pnl': gross_pnl,
                'entry_fee': position_info.get('entry_fee', 0),
                'exit_fee': exit_fee
            }, None

        except Exception as e:
            error_message = str(e)
            logger.error(f"[{coin}] 숏 포지션 청산 중 오류: {error_message}")
            return None, error_message
    
    async def close_long_position(self, coin, price, amount):
        position_info = self.positions.get(coin)
        if not position_info or not position_info.get('entry_price'):
            return None, "유효한 포지션 정보 없음"
            
        exit_strategy = self.config.get('exit_strategy', {})
        
        try:
            if exit_strategy.get('use_limit_order_chase', False):
                logger.info(f"[{coin}] 지정가 추격(Chase)으로 롱 포지션 청산을 시도합니다.")
                initial_order_status, err = await self._execute_chase_limit_order(coin, 'sell', amount, 'long')
                if err: raise Exception(err)
            else:
                logger.info(f"[{coin}] 시장가 주문으로 롱 포지션 청산을 시도합니다.")
                params = {'posSide': 'long', 'reduceOnly': True}
                initial_order_status = await self.okx.create_market_sell_order(f"{coin}/USDT:USDT", amount, params)

            if not initial_order_status or not initial_order_status.get('id'):
                return None, "거래소로부터 유효한 주문 응답을 받지 못했습니다."

            logger.info(f"[{coin}] 청산 주문(ID: {initial_order_status['id']}) 제출 완료. 최종 상태 확인 중...")
            final_order_status = await self._fetch_closed_order_with_retry(initial_order_status['id'], f"{coin}/USDT:USDT")

            if not final_order_status:
                return None, "최종 체결 상태를 확인하지 못했습니다."

            entry_price = position_info['entry_price']
            exit_price = final_order_status.get('average')
            if not exit_price or exit_price <= 0:
                return None, "유효한 평균 체결가를 얻지 못했습니다."

            fee_info = final_order_status.get('fee', {})
            exit_fee = float(fee_info.get('cost', 0)) if fee_info else 0
            filled_amount = final_order_status.get('filled', amount)
            gross_pnl = (exit_price - entry_price) * filled_amount
            net_pnl = gross_pnl - position_info.get('entry_fee', 0) - exit_fee
            
            if net_pnl > 0: self.winning_trades += 1

            logger.info(f"[{coin}] (LIVE) 롱 포지션 청산 완료. PNL: ${net_pnl:.4f}")
            if coin in self.positions: del self.positions[coin]
            
            return {
                'id': final_order_status['id'], 
                'amount': filled_amount, 
                'price': exit_price, 
                'net_pnl': net_pnl,
                'gross_pnl': gross_pnl,
                'entry_fee': position_info.get('entry_fee', 0),
                'exit_fee': exit_fee
            }, None
        except Exception as e:
            error_message = str(e)
            logger.error(f"[{coin}] 롱 포지션 청산 중 오류: {error_message}")
            return None, error_message

    async def close_all_positions(self):
        logger.warning("봇 종료 절차의 일부로 모든 열린 포지션을 청산합니다...")
        try:
            positions_to_close = list(self.positions.keys())
            if not positions_to_close:
                logger.info("청산할 포지션이 없습니다.")
                return

            close_tasks = []
            for symbol in positions_to_close:
                position_info = self.positions.get(symbol)
                if position_info and position_info.get('status') != 'closing':
                    position_info['status'] = 'closing'
                    if position_info['type'] == 'short':
                        task = self.close_short_position(symbol, 0, position_info['amount'])
                    else:
                        task = self.close_long_position(symbol, 0, position_info['amount'])
                    close_tasks.append(task)

            if not close_tasks:
                logger.info("실제로 청산을 시작할 포지션이 없습니다.")
                return

            results = await asyncio.gather(*close_tasks, return_exceptions=True)
            successful_closes = [res for res in results if res and not isinstance(res, Exception)]
            logger.info(f"포지션 일괄 청산 요청 완료: {len(successful_closes)}/{len(close_tasks)}건 성공")

        except Exception as e:
            logger.critical(f"모든 포지션 청산 중 심각한 오류 발생: {e}", exc_info=True)
        
    def display_final_stats(self):
        logger.info("--- 실거래 모드 최종 통계 ---")
        logger.info(f"총 거래 횟수: {self.trade_count}회")
        if self.trade_count > 0:
            win_rate = (self.winning_trades / self.trade_count) * 100
            logger.info(f"승리한 거래: {self.winning_trades}회"); logger.info(f"승률: {win_rate:.2f}%")
        else: logger.info("승률: 거래 기록 없음")
        logger.info("-" * 20)
        final_net_pnl = self.balance - self.initial_balance
        logger.info(f"초기 잔고: ${self.initial_balance:.4f}")
        logger.info(f"최종 순수익 (Net PNL): ${final_net_pnl:.4f}")
        logger.info(f"최종 잔고: ${self.balance:.4f}")

    async def close(self):
        logger.info("봇 종료 절차를 시작합니다...")
        self._is_running = False
        
        await self.close_all_positions()
        await asyncio.sleep(5)

        for task in self._background_tasks: 
            task.cancel()
        await asyncio.gather(*self._background_tasks, return_exceptions=True)
        
        logger.info("모든 작업이 정리되었습니다. 최종 통계를 표시합니다.")
        self.display_final_stats()

        if self.okx:
            await self.okx.close()
            logger.info("OKX 클라이언트 연결을 안전하게 종료했습니다.")