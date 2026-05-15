import asyncio
import json
import websockets
import pyupbit
import ccxt.async_support as ccxt_async
from utils.logger import logger
import time
import httpx
import os

RATE_CACHE_FILE = 'config/exchange_rate_cache.json'

class WebSocketManager:
    def __init__(self, config_manager, position_manager, shared_data):
        self.config_manager = config_manager
        self.position_manager = position_manager
        self.shared_data = shared_data
        self.tradable_symbols = []
        self.latest_prices = {}
        self._update_config_values()

        self.is_demo_mode = os.getenv('DEMO_MODE', 'true').lower() == 'true'
        
        # <<<--- 여기가 수정된 부분입니다 (START) ---
        # 모의 투자 여부와 관계없이 항상 실거래 웹소켓 주소를 사용하도록 고정합니다.
        self.okx_ws_uri = "wss://ws.okx.com:8443/ws/v5/public"
        if self.is_demo_mode:
            logger.info("OKX 웹소켓이 '모의투자' 모드이지만, 데이터는 '실거래(ws)' 서버에서 수신합니다.")
        else:
            logger.info("OKX 웹소켓이 '실거래(ws)' 모드로 설정되었습니다.")
        # <<<--- 여기가 수정된 부분입니다 (END) ---


    @property
    def config(self):
        return self.config_manager.get_config()
    
    def _update_config_values(self):
        config = self.config
        scanner_config = config.get('scanner', {})
        self.min_quote_volume = scanner_config.get('min_quote_volume_usdt', 200000)
        self.indicator_update_interval = config.get('common_settings', {}).get('indicator_update_interval_seconds', 3600)

    async def _exchange_rate_updater_task(self, stop_event):
        api_key = os.getenv('EXCHANGERATE_API_KEY', '').strip()
        if not api_key:
            logger.warning("ExchangeRate-API 키가 설정되지 않아, 환율 업데이트가 비활성화됩니다.")
            return
        url = f"https://v6.exchangerate-api.com/v6/{api_key}/latest/USD"
        update_interval_seconds = 6 * 60 * 60
        try:
            if os.path.exists(RATE_CACHE_FILE):
                with open(RATE_CACHE_FILE, 'r') as f:
                    cache = json.load(f)
                    last_updated = cache.get('timestamp', 0)
                    if time.time() - last_updated < update_interval_seconds:
                        self.shared_data['usdt_krw_rate'] = cache['rate']
                        logger.info(f"✅ 캐시된 환율을 사용합니다: {cache['rate']:,.2f} KRW")
                        await asyncio.sleep(update_interval_seconds - (time.time() - last_updated))
        except Exception as e:
            logger.warning(f"환율 캐시 로드 실패: {e}")
        
        while not stop_event.is_set():
            try:
                logger.info("환율 정보 업데이트를 시도합니다.")
                async with httpx.AsyncClient() as client:
                    response = await client.get(url, timeout=10)
                    response.raise_for_status()
                    data = response.json()
                    if data.get("result") == "success":
                        krw_rate = data['conversion_rates']['KRW']
                        self.shared_data['usdt_krw_rate'] = krw_rate
                        logger.info(f"✅ 실제 외환 환율 업데이트 완료: {krw_rate:,.2f} KRW")
                        with open(RATE_CACHE_FILE, 'w') as f: json.dump({'timestamp': time.time(), 'rate': krw_rate}, f)
                    else:
                        logger.warning(f"환율 API 응답 오류: {data.get('error-type', '알 수 없는 오류')}")
                await asyncio.sleep(update_interval_seconds)
            except Exception as e:
                logger.error(f"환율 업데이트 중 오류 발생: {e}. 1시간 후 재시도합니다.")
                await asyncio.sleep(3600)

    async def _get_tradable_symbols(self):
        okx = None
        try:
            logger.info("거래 가능한 코인 목록 탐색 시작 (선물 시장 기준)...")
            okx = ccxt_async.okx({'options': {'defaultType': 'swap'}})
            
            # <<<--- 여기가 수정된 부분입니다 (START) ---
            # 모의 투자일 때 sandbox_mode로 설정하는 코드를 제거하여 항상 실서버 API를 사용하도록 합니다.
            # if self.is_demo_mode:
            #     okx.set_sandbox_mode(True)
            #     logger.info("ccxt API가 '모의투자(Sandbox)' 모드로 설정되었습니다.")
            # <<<--- 여기가 수정된 부분입니다 (END) ---

            upbit_symbols = {t.split('-')[1] for t in pyupbit.get_tickers(fiat="KRW")}
            logger.info(f"업비트에서 {len(upbit_symbols)}개의 KRW 마켓 코인을 찾았습니다.")
            
            filtered_symbols = []
            
            for symbol_base in upbit_symbols:
                try:
                    okx_symbol = f"{symbol_base}/USDT:USDT"
                    ticker_data = await okx.fetch_ticker(okx_symbol)
                    
                    volume_usdt = ticker_data.get('quoteVolume')
                    if volume_usdt is None:
                        volume_usdt_str = ticker_data.get('info', {}).get('volCcy24h')
                        if volume_usdt_str:
                            volume_usdt = float(volume_usdt_str)
                    
                    if volume_usdt and float(volume_usdt) >= self.min_quote_volume:
                        filtered_symbols.append(symbol_base)
                        
                except ccxt_async.BadSymbol:
                    continue
                except Exception as e:
                    logger.warning(f"'{symbol_base}' 코인 정보 조회 중 오류 발생, 건너뜁니다: {e}")
                    continue

            blacklist = set(self.config.get('blacklist', []))
            self.tradable_symbols = sorted(list(set(filtered_symbols) - blacklist))

        except Exception as e:
            logger.error(f"거래량 필터링 중 심각한 오류 발생: {e}. 기본 코인으로 진행합니다.", exc_info=True)
            self.tradable_symbols = sorted(list(set(["BTC", "ETH", "XRP"]) - set(self.config.get('blacklist', []))))
        finally:
            self.latest_prices = {
                coin: {'upbit_price': None, 'okx_price': None, 'timestamp': 0} 
                for coin in self.tradable_symbols
            }
            logger.info(f"최종 거래 대상 코인 수: {len(self.tradable_symbols)}개 (USDT 제외)")
            if okx:
                await okx.close()

    async def _upbit_handler(self, stop_event):
        uri = "wss://api.upbit.com/websocket/v1"
        if not self.tradable_symbols: return
        upbit_tickers = [f"KRW-{s}" for s in self.tradable_symbols]
        while not stop_event.is_set():
            try:
                async with websockets.connect(uri, ping_interval=20) as websocket:
                    subscribe_fmt = [{"ticket":"kim-bot"},{"type":"ticker","codes": upbit_tickers}]
                    await websocket.send(json.dumps(subscribe_fmt))
                    logger.info("Upbit 웹소켓에 연결 및 구독 요청 완료.")
                    while not stop_event.is_set():
                        data = json.loads(await websocket.recv())
                        coin = data['code'].split('-')[1]
                        if coin in self.latest_prices:
                            self.latest_prices[coin]['upbit_price'] = data['trade_price']
                            self.latest_prices[coin]['timestamp'] = time.time()
            except Exception as e:
                logger.warning(f"Upbit 웹소켓 연결 끊김: {e}. 5초 후 재연결합니다.")
                await asyncio.sleep(5)

    async def _okx_handler(self, stop_event):
        uri = self.okx_ws_uri
        if not self.tradable_symbols: return
        okx_args = [{"channel": "tickers", "instId": f"{s}-USDT-SWAP"} for s in self.tradable_symbols]
        while not stop_event.is_set():
            try:
                async with websockets.connect(uri, ping_interval=20) as websocket:
                    subscribe_fmt = {"op": "subscribe", "args": okx_args}
                    await websocket.send(json.dumps(subscribe_fmt))
                    logger.info("OKX 선물(SWAP) 웹소켓에 연결 및 구독 요청 완료.")
                    while not stop_event.is_set():
                        data = json.loads(await websocket.recv())
                        if 'data' in data:
                            for item in data['data']:
                                if item['instId'].endswith('-USDT-SWAP'):
                                    coin = item['instId'].split('-')[0]
                                    if coin in self.latest_prices and item.get('last'):
                                        self.latest_prices[coin]['okx_price'] = float(item['last'])
                                        self.latest_prices[coin]['timestamp'] = time.time()
            except Exception as e:
                logger.warning(f"OKX 웹소켓 연결 끊김: {e}. 5초 후 재연결합니다.")
                await asyncio.sleep(5)

    async def _price_processor_task(self, stop_event):
        while not stop_event.is_set():
            try:
                await asyncio.sleep(self.config.get('common_settings', {}).get('kimp_calc_interval_seconds', 1.0))
                for coin in self.tradable_symbols:
                    price_data = self.latest_prices.get(coin)
                    if price_data and price_data.get('upbit_price') is not None and price_data.get('okx_price') is not None:
                        await self.position_manager.process_price_update(coin, price_data.copy())
            except Exception as e:
                logger.error(f"가격 처리 태스크에서 오류 발생: {e}", exc_info=True)
                await asyncio.sleep(5)

    async def _indicator_updater_task(self, stop_event):
        if not self.tradable_symbols: return
        while not stop_event.is_set():
            try:
                self._update_config_values()
                await self.position_manager.update_indicators_cache(self.tradable_symbols)
                await asyncio.sleep(self.indicator_update_interval)
            except Exception as e:
                logger.error(f"보조지표 업데이트 태스크에서 오류 발생: {e}")
                await asyncio.sleep(self.indicator_update_interval)
                    
    async def run(self, stop_event):
        self._update_config_values()
        await self._get_tradable_symbols()
        tasks = [
            asyncio.create_task(self._upbit_handler(stop_event)),
            asyncio.create_task(self._okx_handler(stop_event)),
            asyncio.create_task(self._price_processor_task(stop_event)),
            asyncio.create_task(self._indicator_updater_task(stop_event)),
            asyncio.create_task(self._exchange_rate_updater_task(stop_event))
        ]
        return tasks