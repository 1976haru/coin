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
from .mock_simulation import (
    MockBroker, MockBrokerConfig,
    MockAccountState, MockPositionBook, MockMarket, MockExecutionEngine,
)
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
from .okx_adapter import (
    OkxAdapter,
    normalize_okx_inst_id, infer_okx_inst_type,
    to_internal_symbol as okx_to_internal_symbol,
)
from .okx_public import (
    OkxPublicClient, OkxPublicAPIError,
    OkxTransportResponse,
    ALLOWED_INST_TYPES as OKX_ALLOWED_INST_TYPES,
    ALLOWED_BARS as OKX_ALLOWED_BARS,
)
from .okx_account import (
    OkxAccountClient, OkxAccountPermissionError,
    OkxAccountTransportResponse,
)
from .okx_trade import (
    OkxTradeClient, OkxTradeClientCapability, OkxPaperOrderClient,
)
from .okx_rate_limit import (
    OkxApiError, OkxRateLimitState,
    parse_okx_api_error, is_okx_rate_limit_error, should_throttle_okx,
    OKX_RATE_LIMIT_CODES,
)
from .binance_adapter import (
    BinanceAdapter,
    normalize_binance_symbol,
    to_internal_symbol as binance_to_internal_symbol,
    is_supported_binance_quote,
)
from .binance_public import (
    BinancePublicClient, BinancePublicAPIError,
    BinanceTransportResponse,
    BINANCE_PUBLIC_DATA_HOST,
    ALLOWED_KLINE_INTERVALS as BINANCE_ALLOWED_KLINE_INTERVALS,
)
from .binance_account import (
    BinanceAccountClient, BinanceAccountPermissionError,
)
from .binance_trade import (
    BinanceTradeClient, BinanceTradeClientCapability,
)
from .binance_rate_limit import (
    BinanceRateLimitState,
    parse_binance_used_weight, should_throttle_binance,
    DEFAULT_WEIGHT_SOFT_LIMIT as BINANCE_WEIGHT_SOFT_LIMIT,
)
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
    # MockBroker simulation (#24)
    "MockBroker", "MockBrokerConfig",
    "MockAccountState", "MockPositionBook", "MockMarket", "MockExecutionEngine",
    "UpbitAdapter",
    # Upbit #21 보조 모듈
    "UpbitPublicClient", "UpbitPublicAPIError", "UpbitTransportResponse",
    "UpbitAccountClient", "UpbitAccountPermissionError",
    "UpbitAccountTransportResponse",
    "UpbitOrderClient", "UpbitOrderClientCapability",
    "normalize_upbit_market", "to_internal_symbol", "is_krw_market",
    "parse_remaining_req", "should_throttle", "RateLimitState",
    "OkxAdapter",
    # OKX #22 보조 모듈
    "OkxPublicClient", "OkxPublicAPIError", "OkxTransportResponse",
    "OkxAccountClient", "OkxAccountPermissionError",
    "OkxAccountTransportResponse",
    "OkxTradeClient", "OkxTradeClientCapability", "OkxPaperOrderClient",
    "OkxApiError", "OkxRateLimitState",
    "OKX_RATE_LIMIT_CODES", "OKX_ALLOWED_INST_TYPES", "OKX_ALLOWED_BARS",
    "parse_okx_api_error", "is_okx_rate_limit_error", "should_throttle_okx",
    "normalize_okx_inst_id", "infer_okx_inst_type",
    "okx_to_internal_symbol",
    "BinanceAdapter",
    # Binance #23 보조 모듈
    "BinancePublicClient", "BinancePublicAPIError", "BinanceTransportResponse",
    "BinanceAccountClient", "BinanceAccountPermissionError",
    "BinanceTradeClient", "BinanceTradeClientCapability",
    "BinanceRateLimitState",
    "BINANCE_PUBLIC_DATA_HOST", "BINANCE_ALLOWED_KLINE_INTERVALS",
    "BINANCE_WEIGHT_SOFT_LIMIT",
    "parse_binance_used_weight", "should_throttle_binance",
    "normalize_binance_symbol", "binance_to_internal_symbol",
    "is_supported_binance_quote",
    # rate limiter (#26)
    "TokenBucket", "RateLimitSpec", "RATE_LIMITS", "DEFAULT_SPEC",
    "RateLimitExceeded", "RateLimitTimeout",
    "get_limiter_for", "rate_limited",
]
