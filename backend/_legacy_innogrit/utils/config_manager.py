# kim_bot/utils/config_manager.py

import json
import asyncio
import aiohttp
from datetime import datetime, timedelta

from .logger import logger

class ConfigManager:
    def __init__(self, config_path='config/config.json', cache_path='config/exchange_rate_cache.json'):
        self.config_path = config_path
        self.cache_path = cache_path
        self.config = self._load_json(config_path)
        self.cache = self._load_json(cache_path)
        self.update_interval = timedelta(minutes=5)  # 환율 정보 업데이트 간격
        self.next_update_time = datetime.now()
        asyncio.create_task(self._schedule_exchange_rate_update())

    def get_config(self):
        return self.config

    def _load_json(self, file_path):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            logger.warning(f"{file_path} 파일을 찾을 수 없어 기본 설정을 반환합니다.")
            return {}
        except json.JSONDecodeError:
            logger.error(f"{file_path} 파일의 형식이 올바르지 않습니다.")
            return {}

    async def _update_exchange_rate(self):
        logger.info("환율 정보 업데이트를 시도합니다.")
        api_config = self.config.get('exchange_rate_api', {})
        url = api_config.get('url')
        fallback_rate = api_config.get('fallback_rate', 1380.0)

        if not url:
            logger.warning("환율 API URL이 설정되지 않았습니다.")
            self._update_cached_rate(fallback_rate)
            return

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        #logger.info(f"API 응답 수신: {data}") 
                        rate = float(data['conversion_rates']['KRW'])
                        self._update_cached_rate(rate)
                        logger.info(f"환율 정보가 성공적으로 업데이트되었습니다: {rate}")
                    else:
                        logger.error(f"환율 API 요청 실패: 상태 코드 {response.status}")
                        self._update_cached_rate(fallback_rate)
        except Exception as e:
            logger.error(f"환율 정보 업데이트 중 오류 발생: {e}", exc_info=True)
            self._update_cached_rate(fallback_rate)

    def _update_cached_rate(self, rate):
        self.cache['usdt_krw_rate'] = rate
        self.cache['last_updated'] = datetime.now().isoformat()
        try:
            with open(self.cache_path, 'w', encoding='utf-8') as f:
                json.dump(self.cache, f, indent=4)
        except IOError as e:
            logger.error(f"환율 캐시 파일 저장 실패: {e}", exc_info=True)

    def get_exchange_rate(self):
        return self.cache.get('usdt_krw_rate')

    async def _schedule_exchange_rate_update(self):
        while True:
            now = datetime.now()
            if now >= self.next_update_time:
                await self._update_exchange_rate()
                self.next_update_time = now + self.update_interval
            await asyncio.sleep(60) # 1분마다 다음 업데이트 시간 확인