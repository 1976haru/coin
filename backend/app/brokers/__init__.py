"""브로커 어댑터 패키지.

체크리스트 #20-25 (Exchange Adapter Interface, Upbit, OKX, Binance, Mock, Paper).
AI Agent / Strategy / Frontend는 이 패키지를 직접 import 금지.
주문은 반드시 OrderGateway 경유.
"""
from .base import (
    ExchangeAdapter, AdapterCapability, AdapterMode,
    ExchangeAdapterDisabledError,
    conforms_to_market_data_source, assert_no_withdrawal_methods,
)
from .paper_broker import PaperBroker, PaperOrderResult
from .mock_broker import MockExchangeAdapter
from .upbit_adapter import UpbitAdapter
from .okx_adapter import OkxAdapter
from .binance_adapter import BinanceAdapter
from .rate_limiter import (
    TokenBucket, RateLimitSpec, RATE_LIMITS, DEFAULT_SPEC,
    RateLimitExceeded, RateLimitTimeout,
    get_limiter_for, rate_limited,
)

__all__ = [
    "ExchangeAdapter", "AdapterCapability", "AdapterMode",
    "ExchangeAdapterDisabledError",
    "conforms_to_market_data_source", "assert_no_withdrawal_methods",
    "PaperBroker", "PaperOrderResult",
    "MockExchangeAdapter",
    "UpbitAdapter",
    "OkxAdapter",
    "BinanceAdapter",
    # rate limiter (#26)
    "TokenBucket", "RateLimitSpec", "RATE_LIMITS", "DEFAULT_SPEC",
    "RateLimitExceeded", "RateLimitTimeout",
    "get_limiter_for", "rate_limited",
]
