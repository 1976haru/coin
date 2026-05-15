import time
import asyncio
from datetime import datetime, date, timezone, timedelta
import pandas_ta as ta
import pyupbit
import inspect
import uuid
from collections import deque
from utils.logger import logger, TRADE_CYCLE_LOG_HEADER

KST = timezone(timedelta(hours=9))

class PositionManager:
    def __init__(self, config_manager, trade_manager, shared_data, stop_event, signal_csv_logger, trade_result_csv_logger, trades_cycle_csv_logger, balance_info, dynamic_values_shared):
        self.config_manager = config_manager
        self.trade_manager = trade_manager
        self.shared_data = shared_data
        self.stop_event = stop_event
        self.signal_csv_logger = signal_csv_logger
        self.trade_result_csv_logger = trade_result_csv_logger
        self.trades_cycle_csv_logger = trades_cycle_csv_logger
        self.positions = {}
        self.processing_locks = {}
        self.indicators_cache = {}
        self.last_kimp_calc_time = {}
        self.cooldown_cache = {}
        self.permanent_blacklist = set(self.config.get('blacklist', []))
        self.trade_counts_per_symbol = {}
        self.consecutive_losses = 0
        self.daily_trade_count = 0
        self.current_date = date.today()
        self.trading_halted = False
        self.shutdown_initiated = False
        self.last_signal_cache = {}
        self.initial_balance = float(balance_info.get('totalEq', 1000)) if balance_info else 1000
        self.dynamic_entry_cache = {}
        self.kimp_history = {}
        self.all_kimp_rates = {}
        self.dynamic_values_shared = dynamic_values_shared
        self.trade_cycle_data = {}
        
        self._background_task = None
        self._last_checker_heartbeat = 0
        self._main_loop_task = asyncio.create_task(self.start_background_tasks())

    def _get_now_kst_str(self):
        """현재 시간을 KST 기준의 문자열로 반환하는 헬퍼 함수"""
        return datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]

    async def start_background_tasks(self):
        """백그라운드 작업을 시작하고 모니터링하는 메인 루프"""
        logger.info("백그라운드 작업 관리자를 시작합니다.")
        while not self.stop_event.is_set():
            if self._background_task is None or self._background_task.done():
                if self._background_task and self._background_task.done():
                    try:
                        self._background_task.result()
                    except Exception as e:
                        logger.error(f"백그라운드 작업(_periodic_checker)이 예외로 종료되었습니다: {e}. 재시작합니다.", exc_info=True)
                else:
                    logger.info("백그라운드 작업(_periodic_checker)을 시작합니다.")
                
                self._background_task = asyncio.create_task(self._periodic_checker())

            if time.time() - self._last_checker_heartbeat > 180 and self._last_checker_heartbeat != 0:
                 logger.warning("백그라운드 검사기의 하트비트가 3분 이상 감지되지 않습니다. 상태 확인이 필요합니다.")

            await asyncio.sleep(30)

    async def _periodic_checker(self):
        """타임컷과 청산 실패 재시도를 주기적으로 확인하는 독립적인 백그라운드 작업"""
        logger.info("주기적 포지션 검사기 루프가 시작되었습니다.")
        while not self.stop_event.is_set():
            self._last_checker_heartbeat = time.time()
            try:
                if not self.shutdown_initiated:
                    await self.check_positions_for_timeout_and_retry()
            except Exception as e:
                logger.error(f"주기적 검사 중 오류 발생: {e}", exc_info=True)
            
            await asyncio.sleep(15)

    async def _call_maybe_await(self, callable_obj, *args, **kwargs):
        if inspect.iscoroutinefunction(callable_obj) or inspect.iscoroutine(callable_obj):
            return await callable_obj(*args, **kwargs)
        else:
            return callable_obj(*args, **kwargs)

    async def sync_positions_on_startup(self):
        try:
            exchange_positions = await self._call_maybe_await(self.trade_manager.get_open_positions)
            if not exchange_positions:
                logger.info("거래소에 열려있는 포지션이 없어 동기화를 건너뜁니다.")
                return
            logger.warning("봇 재시작으로 인해 거래소에 남아있는 '고아 포지션'을 발견했습니다. 즉시 청산을 준비합니다.")
            for symbol, pos_data in exchange_positions.items():
                self.positions[symbol] = {
                    'type': pos_data['type'], 
                    'amount': pos_data['amount'],
                    'entry_price': pos_data['entry_price'], 
                    'status': 'pending_closure_on_sync',
                    'entry_time': time.time()
                }
                logger.info(f"  -> 청산 대기 포지션: [{symbol}], 타입: {pos_data['type']}, 수량: {pos_data['amount']}")
        except Exception as e:
            logger.error(f"포지션 동기화 중 심각한 오류 발생: {e}", exc_info=True)
            logger.critical("안전을 위해 봇을 종료합니다."); self.stop_event.set()

    @property
    def config(self):
        return self.config_manager.get_config()
    
    def _update_dynamic_entry_threshold(self, coin, kimp_rate):
        config = self.config.get('dynamic_entry', {})
        if not config.get('enabled', False):
            if coin in self.dynamic_entry_cache: del self.dynamic_entry_cache[coin]
            self.dynamic_values_shared['volatility_adjustment'] = 0; return
        now = time.time()
        if coin not in self.kimp_history: self.kimp_history[coin] = deque(maxlen=int(config.get('volatility_window_sec', 60)))
        self.kimp_history[coin].append(kimp_rate)
        cache = self.dynamic_entry_cache.get(coin, {})
        if (now - cache.get('timestamp', 0)) < config.get('hold_sec', 120): return
        history = self.kimp_history[coin]
        if len(history) < history.maxlen: return
        volatility = max(history) - min(history)
        adjustment = 0
        if volatility > config.get('volatility_max_change', 0.1):
            adjustment = config.get('adjustment_bps', -0.05)
            logger.info(f"[{coin}] 김프 변동성 증가({volatility:.3f}%), 진입 기준 보수적으로 조정: {adjustment}%")
        self.dynamic_values_shared['volatility_adjustment'] = adjustment
        self.dynamic_entry_cache[coin] = {'adjustment': adjustment, 'timestamp': now}

    def _process_rule_offsets(self, rules, pos_type, base_kimp=None):
        if not rules: return rules
        rules = rules.copy()
        base = base_kimp if base_kimp is not None else rules.get('entry_kimp_rate')
        if base is None: return rules

        if 'entry_kimp_max_offset' in rules:
            offset = abs(rules['entry_kimp_max_offset'])
            rules['entry_kimp_rate_max'] = base - offset if pos_type == 'short' else base + offset
            
        return rules

    def _reset_daily_counters_if_needed(self):
        today = date.today()
        if today != self.current_date:
            logger.info(f"날짜 변경: {self.current_date} -> {today}. 일일 거래 카운터를 초기화합니다.")
            self.daily_trade_count = 0; self.trade_counts_per_symbol = {}; self.current_date = today

    def is_risky_market(self, indicators, rm_config):
        if not indicators or not rm_config: return False
        if indicators.get('rsi', 0) > rm_config.get('rsi_threshold', 75): return True
        if abs(indicators.get('hourly_change', 0)) > rm_config.get('hourly_change_threshold', 5.0): return True
        if indicators.get('volume_increase_rate', 0) > rm_config.get('volume_increase_rate', 5.0): return True
        return False
    
    async def process_price_update(self, coin, price_data):
        now = time.time()
        if (now - price_data.get('timestamp', 0)) > self.config.get('trading_rules', {}).get('freshness_sec', 5): return
        self._reset_daily_counters_if_needed()
        if coin in self.permanent_blacklist: return
        if (now - self.last_kimp_calc_time.get(coin, 0)) < self.config.get('common_settings', {}).get('kimp_calc_interval_seconds', 1.0): return
        self.last_kimp_calc_time[coin] = now
        upbit_price, okx_price_usdt = price_data.get('upbit_price'), price_data.get('okx_price')
        if not all([upbit_price, okx_price_usdt]): return
        usdt_base_rate = self.shared_data.get('usdt_krw_rate')
        if not usdt_base_rate: return
        kimp_rate = (((upbit_price / okx_price_usdt) / usdt_base_rate) - 1) * 100
        self.all_kimp_rates[coin] = kimp_rate
        
        abnormal_range = self.config.get('abnormal_kimp_range', {})
        if not (abnormal_range.get('min', -50) < kimp_rate < abnormal_range.get('max', 50)):
            self.permanent_blacklist.add(coin)
            logger.warning(f"[{coin}] 비정상 김프({kimp_rate:.2f}%) 감지. 영구 제외 목록에 추가.")
            if coin in self.positions: await self.close_position_wrapper(coin, "abnormal_kimp", kimp_rate, okx_price_usdt)
            return
        
        if coin in self.positions: await self._call_maybe_await(self.check_exit_conditions, coin, kimp_rate, okx_price_usdt)
        else:
            self._update_dynamic_entry_threshold(coin, kimp_rate)
            await self._call_maybe_await(self.check_entry_conditions, coin, kimp_rate, okx_price_usdt)
    
    def _calculate_unrealized_pnl_pct(self, position, current_price):
        entry_price = position.get('entry_price')
        amount = position.get('amount')
        pos_type = position.get('type')
        entry_fee = position.get('entry_fee', 0)

        if not all([entry_price, amount, pos_type, current_price > 0]): return 0

        MIN_ENTRY_VALUE = 0.0001
        entry_value = abs(entry_price * amount)
        if entry_value < MIN_ENTRY_VALUE: return 0

        current_value = abs(current_price * amount)

        gross_pnl = (current_value - entry_value) if pos_type == 'long' else (entry_value - current_value)
        exit_fee_rate = self.config.get('fees', {}).get('okx_taker', 0.0005)
        estimated_exit_fee = current_value * exit_fee_rate
        net_pnl = gross_pnl - entry_fee - estimated_exit_fee

        pnl_pct = (net_pnl / entry_value) * 100 if entry_value != 0 else 0
        return pnl_pct

    async def check_positions_for_timeout_and_retry(self):
        now = time.time()
        for coin, position in list(self.positions.items()):
            if position.get('status') == 'closing_failed':
                retry_delay = 60
                if now - position.get('last_fail_time', 0) > retry_delay:
                    logger.warning(f"[{coin}] 청산 실패 포지션 재시도를 시작합니다.")
                    latest_price = self.shared_data.get('latest_prices', {}).get(coin, {}).get('okx_price', 0)
                    if latest_price > 0:
                        await self.close_position_wrapper(coin, "retry_failed_close", price=latest_price)
                continue

            if position.get('status') in ['open', 'pending_closure_on_sync']:
                entry_time = position.get('entry_time', now)
                rules = self.config.get('short_position_rules') if position.get('type') == 'short' else self.config.get('long_position_rules')
                time_cut_minutes = rules.get('time_cut_minutes', 15)

                if now - entry_time > time_cut_minutes * 60:
                    logger.warning(f"[{coin}] 포지션 보유 시간 초과({time_cut_minutes}분). 강제 청산을 시도합니다.")
                    await self.close_position_wrapper(coin, "time_cut")

    async def check_exit_conditions(self, coin, kimp_rate, price):
        if self.processing_locks.get(coin): return
        position = self.positions.get(coin)
        if not position or position.get('status') != 'open': return
        
        pos_type, exit_reason = position['type'], None
        
        pnl_pct = self._calculate_unrealized_pnl_pct(position, price)
        tp_pnl_pct = self.config.get('trading_rules', {}).get('take_profit_pnl_pct', 0.9)
        sl_pnl_pct = self.config.get('trading_rules', {}).get('stop_loss_pnl_pct', 0.9)

        if pnl_pct >= tp_pnl_pct:
            exit_reason = f"take_profit_{tp_pnl_pct}%_pnl"
        elif pnl_pct <= -abs(sl_pnl_pct): # 항상 양수로 비교하도록 abs() 추가
            exit_reason = f"stop_loss_{sl_pnl_pct}%_pnl"

        if exit_reason:
            await self.close_position_wrapper(coin, exit_reason, kimp_rate=kimp_rate, price=price)
    
    async def close_position_wrapper(self, coin, reason, kimp_rate=0, price=0):
        if self.processing_locks.get(coin):
            logger.debug(f"[{coin}] 현재 다른 작업이 처리 중이므로 청산 요청을 건너뜁니다.")
            return

        self.processing_locks[coin] = True
        try:
            position = self.positions.get(coin)
            if not position or position.get('status') == 'closing':
                return
            
            pos_type = position.get('type', 'N/A')
            
            if price <= 0:
                price = self.shared_data.get('latest_prices', {}).get(coin, {}).get('okx_price', 0)
            if kimp_rate == 0:
                kimp_rate = self.all_kimp_rates.get(coin, 0)
            
            ts_kst = self._get_now_kst_str()
            pos_type_upper = pos_type.upper()
            self.signal_csv_logger.info(f"{ts_kst},CLOSE-{pos_type_upper},{coin},{kimp_rate:.2f},,,,{reason}")
            
            pnl_display = self._calculate_unrealized_pnl_pct(position, price)
            logger.info(f"[{coin}] ({pos_type_upper}) 청산 조건 충족 ({reason}). 현재 PNL: {pnl_display:.3f}%")
            self.positions[coin]['status'] = 'closing'
            self.last_signal_cache[coin] = {'timestamp': ts_kst, 'price': price, 'reason': reason}
            
            await self._call_maybe_await(self.close_position, coin, kimp_rate, price, reason)
        finally:
            self.processing_locks.pop(coin, None)

    async def _handle_close_failure(self, coin, pos_type, kimp_rate, reason, error_message):
        """청산 실패 시 상태를 확인하고 동기화하는 로직"""
        logger.error(f"[{coin}] ({pos_type.upper()}) 청산 시도 중 오류/실패 감지. 원인: {error_message or '유효한 주문 ID를 받지 못했습니다.'} 거래소와 상태 동기화를 시도합니다.")
        
        try:
            exchange_positions = await self._call_maybe_await(self.trade_manager.get_open_positions)
            
            if coin not in exchange_positions:
                logger.warning(f"[{coin}] 청산 실패로 기록되었으나, 거래소에 포지션이 존재하지 않음을 확인했습니다. 성공적으로 청산된 것으로 간주하고 내부 상태를 업데이트합니다.")
                if coin in self.positions:
                    del self.positions[coin]
                
                await self._call_maybe_await(self._check_safety_limits, 0)
                self.cooldown_cache[coin] = time.time()
                
                ts_kst = self._get_now_kst_str()
                action = "exit_long" if pos_type == 'long' else "exit_short"
                self.trade_result_csv_logger.info(f"{ts_kst},{coin},{action},,,{kimp_rate:.2f},{reason}_sync_success")
            else:
                logger.error(f"[{coin}] 거래소 확인 결과, 포지션이 여전히 존재합니다. 청산 실패 상태로 전환합니다.")
                if coin in self.positions:
                    self.positions[coin]['status'] = 'closing_failed'
                    self.positions[coin]['last_fail_time'] = time.time()

        except Exception as sync_e:
            logger.critical(f"[{coin}] 청산 실패 후 상태 동기화 중 심각한 오류 발생: {sync_e}. 안전을 위해 'closing_failed'로 유지합니다.", exc_info=True)
            if coin in self.positions:
                self.positions[coin]['status'] = 'closing_failed'
                self.positions[coin]['last_fail_time'] = time.time()

    async def close_position(self, coin, kimp_rate, price, reason):
        position = self.positions.get(coin)
        if not position: return
        
        trade_id = position.get('trade_id')
        cycle_data = self.trade_cycle_data.get(trade_id, {})

        pos_type, amount_to_close = position['type'], position['amount']
        order_result, error_message = None, ""
        try:
            if pos_type == 'short':
                order_result, error_message = await self._call_maybe_await(self.trade_manager.close_short_position, coin, price, amount_to_close)
            else:
                order_result, error_message = await self._call_maybe_await(self.trade_manager.close_long_position, coin, price, amount_to_close)
        except Exception as e:
            error_message = str(e); logger.error(f"[{coin}] 포지션 종료 중 예외 발생: {e}", exc_info=True)

        if order_result and order_result.get('id'):
            if coin in self.positions: del self.positions[coin]
            net_pnl = order_result.get('net_pnl', 0)
            await self._call_maybe_await(self._check_safety_limits, net_pnl)
            self.cooldown_cache[coin] = time.time()
            ts_kst, action = self._get_now_kst_str(), "exit_long" if pos_type == 'long' else "exit_short"
            price_val = order_result.get('price', price)
            self.trade_result_csv_logger.info(f"{ts_kst},{coin},{action},{price_val:.8f},{amount_to_close:.6f},{kimp_rate:.2f},{reason}")

            if cycle_data:
                entry_value = cycle_data.get('entry_price_avg', 0) * cycle_data.get('qty', 0)
                pnl_pct = (net_pnl / entry_value) * 100 if entry_value != 0 else 0
                
                cycle_data.update({
                    "status": "CLOSED", "exit_reason": reason,
                    "ts_exit_signal": self.last_signal_cache.get(coin, {}).get('timestamp', ''),
                    "ts_exit_filled": ts_kst,
                    "exit_price_signal": self.last_signal_cache.get(coin, {}).get('price', 0),
                    "exit_price_avg": price_val,
                    "fee_exit": order_result.get('exit_fee', 0),
                    "pnl_net": net_pnl, "pnl_pct": pnl_pct, "kimp_exit_signal": kimp_rate,
                })
                
                log_line = ",".join(str(cycle_data.get(h, '')) for h in TRADE_CYCLE_LOG_HEADER.split(','))
                self.trades_cycle_csv_logger.info(log_line)
                if trade_id in self.trade_cycle_data:
                    del self.trade_cycle_data[trade_id]

        else:
            await self._handle_close_failure(coin, pos_type, kimp_rate, reason, error_message)
            
            if trade_id and trade_id in self.trade_cycle_data:
                self.trade_cycle_data[trade_id]['status'] = "CLOSE_FAILED"
                log_line = ",".join(str(self.trade_cycle_data[trade_id].get(h, '')) for h in TRADE_CYCLE_LOG_HEADER.split(','))
                self.trades_cycle_csv_logger.info(log_line)
                del self.trade_cycle_data[trade_id]

        if self.shutdown_initiated and len(self.positions) == 0:
            logger.info("모든 포지션이 청산되었습니다. 프로그램을 안전하게 종료합니다.")
            self.stop_event.set()

    def _is_entry_condition_met(self, kimp_rate, rules, dynamic_adjustment=0):
        if not rules or not rules.get('enabled'): return False
        entry_min = rules.get('entry_kimp_rate')
        if entry_min is not None: entry_min += dynamic_adjustment
        entry_max = rules.get('entry_kimp_rate_max')
        if entry_max is not None: entry_max += dynamic_adjustment
        if entry_max is not None and entry_min is not None:
            return min(entry_min, entry_max) <= kimp_rate <= max(entry_min, entry_max)
        pos_type = rules.get('position_type')
        if pos_type == 'long' and entry_min is not None: return kimp_rate >= entry_min
        elif pos_type == 'short' and entry_min is not None: return kimp_rate <= entry_min
        return False

    async def check_entry_conditions(self, coin, kimp_rate, price):
        if self.processing_locks.get(coin): return
        if self.trading_halted or self.shutdown_initiated: return
        config = self.config; trading_rules = config.get('trading_rules', {})
        if len(self.positions) >= trading_rules.get('max_concurrent_positions', 5): return
        if (time.time() - self.cooldown_cache.get(coin, 0)) < trading_rules.get('re_entry_cooldown_minutes', 15) * 60: return
        if self.daily_trade_count >= trading_rules.get('max_daily_trades', 1000): return
        if self.trade_counts_per_symbol.get(coin, 0) >= trading_rules.get('max_trades_per_symbol', 20): return

        short_rules = self._process_rule_offsets(config.get('short_position_rules', {}), 'short')
        long_rules = self._process_rule_offsets(config.get('long_position_rules', {}), 'long')
        short_rules['position_type'] = 'short'; long_rules['position_type'] = 'long'
        
        dynamic_adjustment = self.dynamic_entry_cache.get(coin, {}).get('adjustment', 0)
        should_enter_short = self._is_entry_condition_met(kimp_rate, short_rules, dynamic_adjustment)
        should_enter_long = self._is_entry_condition_met(kimp_rate, long_rules, dynamic_adjustment)

        if not should_enter_short and not should_enter_long: return

        self.processing_locks[coin] = True
        
        signal_side = "SHORT" if should_enter_short else "LONG"
        ts_kst, indicators, rules = self._get_now_kst_str(), self.indicators_cache.get(coin, {}), short_rules if should_enter_short else long_rules
        entry_min_display = f"{rules.get('entry_kimp_rate'):.2f}" if rules.get('entry_kimp_rate') is not None else "N/A"
        entry_max_display = f"{rules.get('entry_kimp_rate_max'):.2f}" if 'entry_kimp_rate_max' in rules else "N/A"
        self.signal_csv_logger.info(f"{ts_kst},{signal_side},{coin},{kimp_rate:.2f},\"{entry_min_display}~{entry_max_display}\",{indicators.get('rsi', -1):.2f},{indicators.get('volume_increase_rate', -1):.2f},")

        if self.is_risky_market(indicators, config.get('risk_management')):
            logger.warning(f"[{coin}] 진입 신호가 발생했으나, 시장 위험도가 높아 진입하지 않습니다.")
            self.processing_locks[coin] = False; return
        
        self.last_signal_cache[coin] = {'timestamp': ts_kst, 'price': price, 'reason': 'entry_signal'}

        if should_enter_short:
            order_result, error_msg = await self._call_maybe_await(self.trade_manager.enter_short_position, coin, price)
            await self._call_maybe_await(self.open_position, coin, 'short', kimp_rate, order_result, error_msg)
        elif should_enter_long:
            order_result, error_msg = await self._call_maybe_await(self.trade_manager.enter_long_position, coin, price)
            await self._call_maybe_await(self.open_position, coin, 'long', kimp_rate, order_result, error_msg)

    async def open_position(self, coin, pos_type, kimp_rate, order_result, error_msg=None):
        if order_result and order_result.get('id'):
            price_val, amount_val = order_result.get('price', 0), order_result.get('amount', 0)
            entry_fee = await self._call_maybe_await(self.trade_manager.get_last_entry_fee, coin)

            # --- 타임컷 문제 해결을 위한 수정된 로직 ---
            if coin in self.positions:
                # 기존 포지션이 있는 경우 (추가 매수)
                existing_position = self.positions[coin]
                
                # 가중 평균 단가 계산
                old_amount = existing_position['amount']
                old_price = existing_position['entry_price']
                total_amount = old_amount + amount_val
                avg_price = ((old_amount * old_price) + (amount_val * price_val)) / total_amount
                
                # 포지션 정보 업데이트 (entry_time은 그대로 유지)
                existing_position['amount'] = total_amount
                existing_position['entry_price'] = avg_price
                existing_position['entry_fee'] += entry_fee
                logger.info(f"[{coin}] 포지션 추가 매수 완료. 총 수량: {total_amount:.4f}, 평균 단가: {avg_price:.8f}")
            else:
                # 신규 포지션 진입
                self.positions[coin] = {
                    'type': pos_type, 
                    'entry_time': time.time(), # 최초 진입 시간 기록
                    'amount': amount_val, 
                    'entry_price': price_val, 
                    'entry_kimp_rate': kimp_rate, 
                    'status': 'open',
                    'entry_fee': entry_fee 
                }
                # 신규 포지션에 대해서만 거래 사이클 로그 시작
                trade_id = str(uuid.uuid4())
                self.positions[coin]['trade_id'] = trade_id
                rules = self.config.get('short_position_rules') if pos_type == 'short' else self.config.get('long_position_rules')
                ts_kst_filled = self._get_now_kst_str()

                self.trade_cycle_data[trade_id] = {
                    "trade_id": trade_id, "status": "OPEN", "symbol": coin, "side": pos_type,
                    "entry_reason": self.last_signal_cache.get(coin, {}).get('reason', 'N/A'),
                    "ts_entry_signal": self.last_signal_cache.get(coin, {}).get('timestamp', ''),
                    "ts_entry_filled": ts_kst_filled,
                    "entry_price_signal": self.last_signal_cache.get(coin, {}).get('price', 0),
                    "entry_price_avg": price_val, "qty": amount_val, "fee_entry": entry_fee,
                    "kimp_entry_signal": kimp_rate,
                    "kimp_volatility_entry": self.dynamic_values_shared.get('volatility_adjustment', 0),
                    "strategy_params": str(rules).replace(',', ';') 
                }

            # 공통 실행 로직
            self.daily_trade_count += 1
            self.trade_counts_per_symbol[coin] = self.trade_counts_per_symbol.get(coin, 0) + 1
            ts_kst, action = self._get_now_kst_str(), "enter_long" if pos_type == 'long' else "enter_short"
            self.trade_result_csv_logger.info(f"{ts_kst},{coin},{action},{price_val:.8f},{amount_val:.6f},{kimp_rate:.2f},")
            
        else:
            logger.warning(f"[{coin}] ({pos_type.upper()}) 진입 주문이 실패했습니다. 원인: {error_msg or '알 수 없음'}")
        
        self.processing_locks.pop(coin, None)

    async def _check_safety_limits(self, net_pnl):
        if self.trading_halted: return
        trading_rules = self.config.get('trading_rules', {}); max_consecutive_losses = trading_rules.get('max_consecutive_losses', 5)
        account_loss_limit_percent = trading_rules.get('account_loss_limit_percent', 30)
        if net_pnl < 0:
            self.consecutive_losses += 1
            logger.warning(f"연속 손실 기록: {self.consecutive_losses}/{max_consecutive_losses}회")
            if self.consecutive_losses >= max_consecutive_losses:
                self.trading_halted = True; reason = "seq_loss_limit"
                logger.critical(f"안전장치 발동: 최대 연속 손실({max_consecutive_losses}회) 도달. 모든 신규 거래를 중단합니다.")
                self.trade_result_csv_logger.info(f"{self._get_now_kst_str()},,,,,,{reason}")
        else:
            if self.consecutive_losses > 0: logger.info("수익 발생으로 연속 손실 기록을 초기화합니다.")
            self.consecutive_losses = 0
        if not self.trading_halted:
            balance_info = await self._call_maybe_await(self.trade_manager.get_balance)
            if balance_info:
                current_balance = float(balance_info.get('totalEq', self.initial_balance)); pnl_percentage = ((current_balance - self.initial_balance) / self.initial_balance) * 100
                loss_limit = -abs(account_loss_limit_percent)
                if pnl_percentage <= loss_limit:
                    self.trading_halted = True; reason = "day_limit"
                    logger.critical(f"안전장치 발동: 총 계좌 손실률 {pnl_percentage:.2f}% 도달 (제한: {loss_limit:.2f}%). 모든 신규 거래를 중단합니다.")
                    self.trade_result_csv_logger.info(f"{self._get_now_kst_str()},,,,,,{reason}")

    async def update_indicators_cache(self, symbols):
        logger.info("모든 코인의 보조지표(RSI 등)를 업데이트합니다...")
        def fetch_ohlcv(coin):
            try:
                df = pyupbit.get_ohlcv(f"KRW-{coin}", interval="minute60", count=30)
                if df is None or len(df) < 25: return None
                return {"rsi": ta.rsi(df['close'], length=14).iloc[-1],
                        "hourly_change": (df['close'].iloc[-1] / df['close'].iloc[-2] - 1) * 100,
                        "volume_increase_rate": (df['volume'].iloc[-1] / df['volume'].rolling(window=24).mean().iloc[-1])}
            except Exception as e:
                logger.warning(f"[{coin}] 보조지표 계산 중 오류 발생: {e}"); return None
        tasks = [asyncio.to_thread(fetch_ohlcv, coin) for coin in symbols if coin != 'USDT' and coin not in self.permanent_blacklist]
        results = await asyncio.gather(*tasks)
        for coin, indicators in zip([s for s in symbols if s != 'USDT' and s not in self.permanent_blacklist], results):
            if indicators: self.indicators_cache[coin] = indicators
        logger.info("보조지표 업데이트를 완료했습니다.")