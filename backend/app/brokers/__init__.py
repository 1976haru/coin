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
from .upbit_adapter import (
    UpbitAdapter,
    normalize_upbit_market, to_internal_symbol, is_krw_market,
)
from .upbit_public import (
    UpbitPublicClient, UpbitPublicAPIError,
    TransportResponse as UpbitTransportResponse,
)
from .upbit_account import (
    UpbitAccountClient, UpbitAccountPermissionError,
    AccountTransportResponse as UpbitAccountTransportResponse,
)
from .upbit_order import UpbitOrderClient, UpbitOrderClientCapability
from .upbit_rate_limit import (
    parse_remaining_req, should_throttle, RateLimitState,
)
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
    # Upbit #21 보조 모듈
    "UpbitPublicClient", "UpbitPublicAPIError", "UpbitTransportResponse",
    "UpbitAccountClient", "UpbitAccountPermissionError",
    "UpbitAccountTransportResponse",
    "UpbitOrderClient", "UpbitOrderClientCapability",
    "normalize_upbit_market", "to_internal_symbol", "is_krw_market",
    "parse_remaining_req", "should_throttle", "RateLimitState",
    "OkxAdapter",
    "BinanceAdapter",
    # rate limiter (#26)
    "TokenBucket", "RateLimitSpec", "RATE_LIMITS", "DEFAULT_SPEC",
    "RateLimitExceeded", "RateLimitTimeout",
    "get_limiter_for", "rate_limited",
]
