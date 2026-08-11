from __future__ import annotations

import json
import math
import time
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .rest_errors import WeexRestBusinessError, raise_for_weex_business_error

SPOT_BASE_URL = "https://api-spot.weex.com"
FUTURES_BASE_URL = "https://api-contract.weex.com"
SPOT_SYMBOL_PRICE_PATH = "/api/v3/market/ticker/price"
FUTURES_SYMBOL_PRICE_PATH = "/capi/v3/market/symbolPrice"
SPOT_DEPTH_PATH = "/api/v3/market/depth"
FUTURES_DEPTH_PATH = "/capi/v3/market/depth"
SPOT_KLINES_PATH = "/api/v3/market/klines"
FUTURES_KLINES_PATH = "/capi/v3/market/klines"
SPOT_EXCHANGE_INFO_PATH = "/api/v3/exchangeInfo"
FUTURES_EXCHANGE_INFO_PATH = "/capi/v3/market/exchangeInfo"
SPOT_TICKER_24H_PATH = "/api/v3/market/ticker/24hr"
FUTURES_TICKER_24H_PATH = "/capi/v3/market/ticker/24hr"
SPOT_BOOK_TICKER_PATH = "/api/v3/market/ticker/bookTicker"
FUTURES_BOOK_TICKER_PATH = "/capi/v3/market/ticker/bookTicker"
SPOT_RECENT_TRADES_PATH = "/api/v3/market/trades"
FUTURES_RECENT_TRADES_PATH = "/capi/v3/market/trades"
FUTURES_CURRENT_FUNDING_PATH = "/capi/v3/market/premiumIndex"
FUTURES_FUNDING_HISTORY_PATH = "/capi/v3/market/fundingRate"
FUTURES_OPEN_INTEREST_PATH = "/capi/v3/market/openInterest"
FUTURES_HISTORY_KLINES_PATH = "/capi/v3/market/historyKlines"
FUTURES_INDEX_KLINES_PATH = "/capi/v3/market/indexPriceKlines"
FUTURES_MARK_KLINES_PATH = "/capi/v3/market/markPriceKlines"
SPOT_COINS_PATH = "/api/v3/coins"
SPOT_API_SYMBOLS_PATH = "/api/v3/apiTradingSymbols"
FUTURES_API_SYMBOLS_PATH = "/capi/v3/market/apiTradingSymbols"

DEPTH_LIMITS = {"15", "200"}
SPOT_KLINE_INTERVALS = {"1m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "1w"}
FUTURES_KLINE_INTERVALS = {"1m", "5m", "15m", "30m", "1h", "4h", "12h", "1d", "1w"}
_DAY_MS = 24 * 60 * 60 * 1000


class MarketPriceAdapter(Protocol):
    def get_symbol_price(self, *, market: str, symbol: str, price_type: str | None = None) -> dict[str, Any]:
        ...

    def get_depth(self, *, market: str, symbol: str, limit: str | None = None) -> dict[str, Any]:
        ...

    def get_klines(
        self,
        *,
        market: str,
        symbol: str,
        interval: str,
        start_time: str | None = None,
        end_time: str | None = None,
        limit: str | None = None,
    ) -> dict[str, Any]:
        ...

    def get_exchange_info(self, *, market: str, symbol: str | None = None) -> dict[str, Any]:
        ...

    def get_ticker_24h(self, *, market: str, symbol: str | None = None) -> dict[str, Any]:
        ...

    def get_book_ticker(self, *, market: str, symbol: str | None = None) -> dict[str, Any]:
        ...

    def get_recent_trades(self, *, market: str, symbol: str, limit: str | None = None) -> dict[str, Any]:
        ...

    def get_funding_rate(
        self,
        *,
        market: str,
        mode: str,
        symbol: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        limit: str | None = None,
    ) -> dict[str, Any]:
        ...

    def get_open_interest(self, *, market: str, symbol: str) -> dict[str, Any]:
        ...

    def get_advanced_klines(
        self,
        *,
        market: str,
        kind: str,
        symbol: str,
        interval: str,
        start_time: str | None = None,
        end_time: str | None = None,
        limit: str | None = None,
        price_type: str | None = None,
    ) -> dict[str, Any]:
        ...

    def get_catalog(self, *, market: str, catalog: str) -> dict[str, Any]:
        ...


Transport = Callable[[str, float], bytes]


def normalize_market_symbol(symbol: str) -> str:
    normalized = symbol.strip().upper().replace("-", "").replace("/", "").replace(" ", "").replace("_", "")
    if not normalized or not normalized.isalnum():
        raise ValueError("Unsupported symbol format.")
    return normalized


class WeexMarketRestAdapter:
    """WEEX public REST market adapter.

    This adapter only calls unauthenticated public market endpoints. Account and
    Trade adapters must remain separate because they require credential signing.
    """

    def __init__(
        self,
        *,
        spot_base_url: str = SPOT_BASE_URL,
        futures_base_url: str = FUTURES_BASE_URL,
        timeout_seconds: float = 5.0,
        transport: Transport | None = None,
        max_attempts: int = 3,
        retry_base_delay_seconds: float = 0.1,
        sleep_func: Callable[[float], None] | None = None,
        time_func: Callable[[], float] | None = None,
    ):
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts <= 0:
            raise ValueError("max_attempts must be a positive integer.")
        if not math.isfinite(retry_base_delay_seconds) or retry_base_delay_seconds < 0:
            raise ValueError("retry_base_delay_seconds must be a non-negative finite number.")
        self.spot_base_url = spot_base_url.rstrip("/")
        self.futures_base_url = futures_base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.transport = transport or _default_transport
        self.max_attempts = max_attempts
        self.retry_base_delay_seconds = retry_base_delay_seconds
        self.sleep_func = sleep_func or time.sleep
        self.time_func = time_func or time.time

    def get_symbol_price(self, *, market: str, symbol: str, price_type: str | None = None) -> dict[str, Any]:
        normalized_market = _normalize_market(market)
        normalized_symbol = normalize_market_symbol(symbol)
        normalized_price_type = _normalize_price_type(normalized_market, price_type)
        url = self._build_symbol_price_url(
            market=normalized_market,
            symbol=normalized_symbol,
            price_type=normalized_price_type,
        )
        raw_bytes = self._transport_get(url)
        raw = json.loads(raw_bytes.decode("utf-8"))
        raise_for_weex_business_error(raw)
        price = _extract_price(raw)
        if price is None:
            raise WeexRestBusinessError(code="missing_price", message="WEEX market response did not contain a price.", payload=raw)
        try:
            price_number = Decimal(price)
        except (InvalidOperation, ValueError):
            raise WeexRestBusinessError(code="invalid_response", message="WEEX market response contained an invalid price.", payload=raw) from None
        if not price.strip() or not price_number.is_finite() or price_number <= 0:
            raise WeexRestBusinessError(code="invalid_response", message="WEEX market response contained an invalid price.", payload=raw)
        response_symbol = _extract_symbol(raw, fallback=normalized_symbol)
        if response_symbol != normalized_symbol:
            raise WeexRestBusinessError(
                code="invalid_response",
                message="WEEX market response symbol did not match the requested symbol.",
                payload=raw,
            )
        return {
            "market": normalized_market,
            "symbol": response_symbol,
            "price": price,
            "source": "weex_public_rest",
            "endpoint": "spot.market.get_ticker_info" if normalized_market == "spot" else "market.get_symbol_price",
            "raw": raw,
        }

    def get_depth(self, *, market: str, symbol: str, limit: str | None = None) -> dict[str, Any]:
        normalized_market = _normalize_market(market)
        normalized_symbol = normalize_market_symbol(symbol)
        normalized_limit = _normalize_depth_limit(limit)
        path = SPOT_DEPTH_PATH if normalized_market == "spot" else FUTURES_DEPTH_PATH
        query = {"symbol": normalized_symbol}
        if normalized_limit is not None:
            query["limit"] = normalized_limit
        raw = self._public_get(market=normalized_market, path=path, query=query)
        payload = _extract_payload(raw)
        mapping = _first_mapping(payload)
        if mapping is None or not isinstance(mapping.get("bids"), list) or not isinstance(mapping.get("asks"), list):
            raise WeexRestBusinessError(
                code="invalid_response",
                message="WEEX depth response did not contain bids and asks lists.",
                payload=raw,
            )
        if not _valid_depth_levels(mapping["bids"]) or not _valid_depth_levels(mapping["asks"]):
            raise WeexRestBusinessError(
                code="invalid_response",
                message="WEEX depth response contained an invalid price level.",
                payload=raw,
            )
        return {
            "market": normalized_market,
            "symbol": normalized_symbol,
            "bids": mapping.get("bids", []),
            "asks": mapping.get("asks", []),
            "source": "weex_public_rest",
            "endpoint": "spot.market.get_depth_data" if normalized_market == "spot" else "market.get_depth_data",
            "raw": raw,
        }

    def get_klines(
        self,
        *,
        market: str,
        symbol: str,
        interval: str,
        start_time: str | None = None,
        end_time: str | None = None,
        limit: str | None = None,
    ) -> dict[str, Any]:
        normalized_market = _normalize_market(market)
        normalized_symbol = normalize_market_symbol(symbol)
        normalized_interval = _normalize_kline_interval(normalized_market, interval)
        path = SPOT_KLINES_PATH if normalized_market == "spot" else FUTURES_KLINES_PATH
        query = {"symbol": normalized_symbol, "interval": normalized_interval}
        if normalized_market == "futures":
            _put_if_present(query, "limit", _normalize_integer_range(limit, field="limit", minimum=1, maximum=1000))
        raw = self._public_get(market=normalized_market, path=path, query=query)
        payload = _extract_payload(raw)
        if not isinstance(payload, list):
            raise WeexRestBusinessError(
                code="invalid_response",
                message="WEEX K-line response was not a list.",
                payload=raw,
            )
        if not all(_valid_kline_row(row) for row in payload):
            raise WeexRestBusinessError(
                code="invalid_response",
                message="WEEX K-line response contained an invalid row.",
                payload=raw,
            )
        return {
            "market": normalized_market,
            "symbol": normalized_symbol,
            "interval": normalized_interval,
            "klines": payload,
            "source": "weex_public_rest",
            "endpoint": "spot.market.get_k_line_data" if normalized_market == "spot" else "market.get_klines",
            "raw": raw,
        }

    def get_exchange_info(self, *, market: str, symbol: str | None = None) -> dict[str, Any]:
        normalized_market = _normalize_market(market)
        path = SPOT_EXCHANGE_INFO_PATH if normalized_market == "spot" else FUTURES_EXCHANGE_INFO_PATH
        query: dict[str, str] = {}
        if symbol:
            query["symbol"] = normalize_market_symbol(symbol)
        raw = self._public_get(market=normalized_market, path=path, query=query)
        payload = _extract_payload(raw)
        symbols = _extract_symbols(payload)
        if symbols is None:
            raise WeexRestBusinessError(
                code="invalid_response",
                message="WEEX exchange-info response did not contain symbol information.",
                payload=raw,
            )
        requested_symbol = query.get("symbol")
        if requested_symbol is not None and not _contains_symbol(symbols, requested_symbol):
            raise WeexRestBusinessError(
                code="invalid_response",
                message="WEEX exchange-info response did not contain the requested symbol.",
                payload=raw,
            )
        return {
            "market": normalized_market,
            "symbol": requested_symbol,
            "symbols": symbols,
            "source": "weex_public_rest",
            "endpoint": "spot.config.get_product_info" if normalized_market == "spot" else "market.get_contract_info",
            "raw": raw,
        }

    def get_ticker_24h(self, *, market: str, symbol: str | None = None) -> dict[str, Any]:
        normalized_market = _normalize_market(market)
        normalized_symbol = normalize_market_symbol(symbol) if symbol else None
        path = SPOT_TICKER_24H_PATH if normalized_market == "spot" else FUTURES_TICKER_24H_PATH
        query: dict[str, str] = {}
        _put_if_present(query, "symbol", normalized_symbol)
        raw = self._public_get(market=normalized_market, path=path, query=query)
        items = _mapping_items(_extract_payload(raw), resource="ticker", payload=raw)
        _require_mapping_fields(items, fields=("symbol", "lastPrice"), resource="ticker", payload=raw)
        _require_numeric_fields(items, fields=("lastPrice",), resource="ticker", payload=raw, allow_zero=False)
        _require_requested_symbol(items, normalized_symbol, payload=raw)
        return {
            "market": normalized_market,
            "symbol": normalized_symbol,
            "tickers": items,
            "source": "weex_public_rest",
            "endpoint": "spot.market.get_all_ticker_info" if normalized_market == "spot" else "market.get_ticker24h",
            "raw": raw,
        }

    def get_book_ticker(self, *, market: str, symbol: str | None = None) -> dict[str, Any]:
        normalized_market = _normalize_market(market)
        normalized_symbol = normalize_market_symbol(symbol) if symbol else None
        path = SPOT_BOOK_TICKER_PATH if normalized_market == "spot" else FUTURES_BOOK_TICKER_PATH
        query: dict[str, str] = {}
        _put_if_present(query, "symbol", normalized_symbol)
        raw = self._public_get(market=normalized_market, path=path, query=query)
        items = _mapping_items(_extract_payload(raw), resource="book ticker", payload=raw)
        _require_mapping_fields(
            items,
            fields=("symbol", "bidPrice", "bidQty", "askPrice", "askQty"),
            resource="book ticker",
            payload=raw,
        )
        _require_numeric_fields(
            items,
            fields=("bidPrice", "bidQty", "askPrice", "askQty"),
            resource="book ticker",
            payload=raw,
            allow_zero=False,
        )
        _require_requested_symbol(items, normalized_symbol, payload=raw)
        return {
            "market": normalized_market,
            "symbol": normalized_symbol,
            "book_tickers": items,
            "source": "weex_public_rest",
            "endpoint": "spot.market.get_book_ticker" if normalized_market == "spot" else "market.get_book_ticker",
            "raw": raw,
        }

    def get_recent_trades(self, *, market: str, symbol: str, limit: str | None = None) -> dict[str, Any]:
        normalized_market = _normalize_market(market)
        normalized_symbol = normalize_market_symbol(symbol)
        normalized_limit = _normalize_integer_range(limit, field="limit", minimum=1, maximum=1000)
        path = SPOT_RECENT_TRADES_PATH if normalized_market == "spot" else FUTURES_RECENT_TRADES_PATH
        query = {"symbol": normalized_symbol}
        _put_if_present(query, "limit", normalized_limit)
        raw = self._public_get(market=normalized_market, path=path, query=query)
        items = _mapping_items(_extract_payload(raw), resource="recent trades", payload=raw, allow_single=False)
        _require_mapping_fields(items, fields=("id", "price", "qty", "time"), resource="recent trades", payload=raw)
        # STG returns opaque UUIDs for trade IDs; only the timestamp is an integer.
        _require_identifier_fields(items, fields=("id",), resource="recent trades", payload=raw)
        _require_integer_fields(items, fields=("time",), resource="recent trades", payload=raw)
        _require_numeric_fields(items, fields=("price", "qty"), resource="recent trades", payload=raw, allow_zero=False)
        return {
            "market": normalized_market,
            "symbol": normalized_symbol,
            "trades": items,
            "source": "weex_public_rest",
            "endpoint": "spot.market.get_trade_data" if normalized_market == "spot" else "market.get_recent_trades",
            "raw": raw,
        }

    def get_funding_rate(
        self,
        *,
        market: str,
        mode: str,
        symbol: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        limit: str | None = None,
    ) -> dict[str, Any]:
        if _normalize_market(market) != "futures":
            raise ValueError("Funding-rate queries only support futures.")
        normalized_mode = str(mode).strip().lower()
        if normalized_mode not in {"current", "history"}:
            raise ValueError("mode must be current or history.")
        normalized_symbol = normalize_market_symbol(symbol) if symbol else None
        query: dict[str, str] = {}
        if normalized_mode == "current":
            if any(value not in (None, "") for value in (start_time, end_time, limit)):
                raise ValueError("Current funding rate does not accept history filters.")
            path = FUTURES_CURRENT_FUNDING_PATH
        else:
            if normalized_symbol is None:
                raise ValueError("symbol is required for funding-rate history.")
            start, end = _normalize_time_range(start_time, end_time, maximum_span_ms=7 * _DAY_MS)
            path = FUTURES_FUNDING_HISTORY_PATH
            _put_if_present(query, "symbol", normalized_symbol)
            _put_if_present(query, "startTime", start)
            _put_if_present(query, "endTime", end)
            _put_if_present(query, "limit", _normalize_integer_range(limit, field="limit", minimum=1, maximum=1000))
        if normalized_mode == "current":
            _put_if_present(query, "symbol", normalized_symbol)
        raw = self._public_get(market="futures", path=path, query=query)
        items = _mapping_items(_extract_payload(raw), resource="funding rates", payload=raw)
        fields = ("symbol", "lastFundingRate") if normalized_mode == "current" else ("symbol", "fundingRate", "fundingTime")
        _require_mapping_fields(items, fields=fields, resource="funding rates", payload=raw)
        _require_numeric_fields(
            items,
            fields=("lastFundingRate",) if normalized_mode == "current" else ("fundingRate",),
            resource="funding rates",
            payload=raw,
            allow_zero=True,
            allow_negative=True,
        )
        if normalized_mode == "history":
            _require_integer_fields(items, fields=("fundingTime",), resource="funding rates", payload=raw)
        _require_requested_symbol(items, normalized_symbol, payload=raw)
        return {
            "market": "futures",
            "mode": normalized_mode,
            "symbol": normalized_symbol,
            "funding_rates": items,
            "source": "weex_public_rest",
            "endpoint": "market.get_current_funding_rate" if normalized_mode == "current" else "market.get_funding_rate_history",
            "raw": raw,
        }

    def get_open_interest(self, *, market: str, symbol: str) -> dict[str, Any]:
        if _normalize_market(market) != "futures":
            raise ValueError("Open-interest queries only support futures.")
        normalized_symbol = normalize_market_symbol(symbol)
        raw = self._public_get(market="futures", path=FUTURES_OPEN_INTEREST_PATH, query={"symbol": normalized_symbol})
        items = _mapping_items(_extract_payload(raw), resource="open interest", payload=raw)
        _require_mapping_fields(items, fields=("symbol", "openInterest"), resource="open interest", payload=raw)
        _require_numeric_fields(items, fields=("openInterest",), resource="open interest", payload=raw, allow_zero=True)
        _require_requested_symbol(items, normalized_symbol, payload=raw)
        return {
            "market": "futures",
            "symbol": normalized_symbol,
            "open_interest": items[0],
            "source": "weex_public_rest",
            "endpoint": "market.get_open_interest",
            "raw": raw,
        }

    def get_advanced_klines(
        self,
        *,
        market: str,
        kind: str,
        symbol: str,
        interval: str,
        start_time: str | None = None,
        end_time: str | None = None,
        limit: str | None = None,
        price_type: str | None = None,
    ) -> dict[str, Any]:
        if _normalize_market(market) != "futures":
            raise ValueError("Advanced K-line queries only support futures.")
        normalized_kind = str(kind).strip().lower()
        paths = {
            "history": FUTURES_HISTORY_KLINES_PATH,
            "index": FUTURES_INDEX_KLINES_PATH,
            "mark": FUTURES_MARK_KLINES_PATH,
        }
        if normalized_kind not in paths:
            raise ValueError("kind must be history, index, or mark.")
        normalized_symbol = normalize_market_symbol(symbol)
        normalized_interval = _normalize_kline_interval("futures", interval)
        query: dict[str, str] = {"symbol": normalized_symbol, "interval": normalized_interval}
        maximum = 100 if normalized_kind == "history" else 1000
        if normalized_kind == "history":
            start, end = _normalize_time_range(start_time, end_time)
            if end is not None and int(end) > int(self.time_func() * 1000):
                raise ValueError("end_time must not be in the future.")
            _put_if_present(query, "startTime", start)
            _put_if_present(query, "endTime", end)
        elif any(value not in (None, "") for value in (start_time, end_time, price_type)):
            raise ValueError("index/mark K-lines do not accept time or price_type filters.")
        _put_if_present(query, "limit", _normalize_integer_range(limit, field="limit", minimum=1, maximum=maximum))
        if normalized_kind == "history" and price_type not in (None, ""):
            normalized_price_type = str(price_type).strip().upper()
            if normalized_price_type not in {"LAST", "INDEX", "MARK"}:
                raise ValueError("price_type must be LAST, INDEX, or MARK.")
            query["priceType"] = normalized_price_type
        raw = self._public_get(market="futures", path=paths[normalized_kind], query=query)
        payload = _extract_payload(raw)
        if not isinstance(payload, list) or not all(_valid_kline_row(row) for row in payload):
            raise WeexRestBusinessError(code="invalid_response", message="WEEX advanced K-line response was invalid.", payload=raw)
        return {
            "market": "futures",
            "kind": normalized_kind,
            "symbol": normalized_symbol,
            "interval": normalized_interval,
            "klines": payload,
            "source": "weex_public_rest",
            "endpoint": f"market.get_{normalized_kind}_price_klines" if normalized_kind != "history" else "market.get_history_klines",
            "raw": raw,
        }

    def get_catalog(self, *, market: str, catalog: str) -> dict[str, Any]:
        normalized_market = _normalize_market(market)
        normalized_catalog = str(catalog).strip().lower()
        if normalized_catalog not in {"coins", "symbols"}:
            raise ValueError("catalog must be coins or symbols.")
        if normalized_market == "futures" and normalized_catalog == "coins":
            raise ValueError("Futures does not expose a coins catalog.")
        path = (
            SPOT_COINS_PATH
            if normalized_catalog == "coins"
            else SPOT_API_SYMBOLS_PATH if normalized_market == "spot" else FUTURES_API_SYMBOLS_PATH
        )
        raw = self._public_get(market=normalized_market, path=path, query={})
        items = _extract_payload(raw)
        if not isinstance(items, list):
            raise WeexRestBusinessError(code="invalid_response", message="WEEX catalog response was not a list.", payload=raw)
        if normalized_catalog == "symbols":
            if not all(isinstance(item, str) and item.strip() for item in items):
                raise WeexRestBusinessError(code="invalid_response", message="WEEX symbol catalog contained an invalid item.", payload=raw)
        elif not all(isinstance(item, dict) and isinstance(item.get("coin"), str) and item["coin"].strip() for item in items):
            raise WeexRestBusinessError(code="invalid_response", message="WEEX coin catalog contained an invalid item.", payload=raw)
        return {
            "market": normalized_market,
            "catalog": normalized_catalog,
            "items": items,
            "source": "weex_public_rest",
            "endpoint": "spot.config.currency_info" if normalized_catalog == "coins" else (
                "spot.config.get_api_trading_symbols" if normalized_market == "spot" else "market.get_api_trading_symbols"
            ),
            "raw": raw,
        }

    def _build_symbol_price_url(self, *, market: str, symbol: str, price_type: str | None) -> str:
        if market == "spot":
            return f"{self.spot_base_url}{SPOT_SYMBOL_PRICE_PATH}?{urlencode({'symbol': symbol})}"
        query: dict[str, str] = {"symbol": symbol}
        if price_type:
            query["priceType"] = price_type
        return f"{self.futures_base_url}{FUTURES_SYMBOL_PRICE_PATH}?{urlencode(query)}"

    def _public_get(self, *, market: str, path: str, query: dict[str, str]) -> Any:
        base_url = self.spot_base_url if market == "spot" else self.futures_base_url
        query_string = urlencode(query)
        url = f"{base_url}{path}"
        if query_string:
            url = f"{url}?{query_string}"
        raw_bytes = self._transport_get(url)
        raw = json.loads(raw_bytes.decode("utf-8"))
        raise_for_weex_business_error(raw)
        return raw

    def _transport_get(self, url: str) -> bytes:
        for attempt in range(self.max_attempts):
            try:
                return self.transport(url, self.timeout_seconds)
            except HTTPError:
                raise
            except (TimeoutError, URLError):
                if attempt + 1 >= self.max_attempts:
                    raise
                self.sleep_func(self.retry_base_delay_seconds * (2**attempt))
        raise RuntimeError("unreachable market retry state")


def _default_transport(url: str, timeout_seconds: float) -> bytes:
    request = Request(url, headers={"User-Agent": "weex-mcp/0.1 public-market"})
    with urlopen(request, timeout=timeout_seconds) as response:
        return response.read()


def _normalize_market(market: str) -> str:
    value = str(market).strip().lower()
    if value == "contract":
        value = "futures"
    if value not in {"spot", "futures"}:
        raise ValueError("Unsupported market.")
    return value


def _normalize_price_type(market: str, price_type: str | None) -> str | None:
    if price_type in (None, ""):
        return None
    normalized = str(price_type).strip().upper()
    if market == "spot":
        raise ValueError("spot symbol price does not support price_type.")
    if normalized not in {"INDEX", "MARK"}:
        raise ValueError("futures price_type must be INDEX or MARK.")
    return normalized


def _normalize_depth_limit(limit: str | None) -> str | None:
    if limit in (None, ""):
        return None
    normalized = str(limit)
    if normalized not in DEPTH_LIMITS:
        raise ValueError("depth limit must be 15 or 200.")
    return normalized


def _normalize_kline_interval(market: str, interval: str) -> str:
    normalized = str(interval).strip()
    allowed = SPOT_KLINE_INTERVALS if market == "spot" else FUTURES_KLINE_INTERVALS
    if normalized not in allowed:
        raise ValueError(f"Unsupported {market} K-line interval.")
    return normalized


def _normalize_integer_range(value: Any, *, field: str, minimum: int, maximum: int) -> str | None:
    if value in (None, ""):
        return None
    normalized = str(value)
    if not normalized.isascii() or not normalized.isdigit() or (len(normalized) > 1 and normalized.startswith("0")):
        raise ValueError(f"{field} must be a canonical integer string.")
    parsed = int(normalized)
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}.")
    return normalized


def _extract_payload(raw: Any) -> Any:
    if isinstance(raw, dict) and "data" in raw:
        return raw["data"]
    return raw


def _first_mapping(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return value[0]
    return None


def _extract_symbol(raw: Any, *, fallback: str) -> str:
    payload = _first_mapping(_extract_payload(raw))
    if payload is None:
        return fallback
    return str(payload.get("symbol") or payload.get("s") or fallback).upper()


def _extract_price(raw: Any) -> str | None:
    payload = _first_mapping(_extract_payload(raw))
    if payload is None:
        return None
    for key in ("price", "lastPrice", "markPrice", "indexPrice"):
        value = payload.get(key)
        if value is not None:
            return str(value)
    return None


def _put_if_present(query: dict[str, str], key: str, value: Any | None) -> None:
    if value is not None and value != "":
        query[key] = str(value)


def _extract_symbols(payload: Any) -> list[Any] | None:
    if isinstance(payload, dict):
        if "symbols" in payload:
            value = payload["symbols"]
            return value if isinstance(value, list) else None
        if "symbol" not in payload:
            return None
        value = payload["symbol"]
        if isinstance(value, list):
            return value
        if value not in (None, ""):
            return [value]
    if isinstance(payload, list):
        return payload
    return None


def _contains_symbol(symbols: list[Any], requested_symbol: str) -> bool:
    for item in symbols:
        if isinstance(item, dict):
            candidate = item.get("symbol") or item.get("s")
        else:
            candidate = item
        if not isinstance(candidate, str):
            continue
        try:
            if normalize_market_symbol(candidate) == requested_symbol:
                return True
        except ValueError:
            continue
    return False


def _valid_depth_levels(levels: list[Any]) -> bool:
    return all(
        isinstance(level, list)
        and len(level) >= 2
        and _valid_number(level[0], allow_zero=False)
        and _valid_number(level[1], allow_zero=False)
        for level in levels
    )


def _valid_kline_row(row: Any) -> bool:
    return (
        isinstance(row, list)
        and len(row) >= 6
        and _valid_number(row[0], allow_zero=True)
        and all(_valid_number(value, allow_zero=False) for value in row[1:5])
        and _valid_number(row[5], allow_zero=True)
    )


def _valid_number(value: Any, *, allow_zero: bool, allow_negative: bool = False) -> bool:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return False
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return False
    if not number.is_finite():
        return False
    if allow_negative:
        return number != 0 if not allow_zero else True
    return number >= 0 if allow_zero else number > 0


def _normalize_time_range(
    start_time: Any,
    end_time: Any,
    *,
    maximum_span_ms: int | None = None,
) -> tuple[str | None, str | None]:
    if (start_time in (None, "")) != (end_time in (None, "")):
        raise ValueError("start_time and end_time must be provided together.")
    start = _normalize_integer_range(start_time, field="start_time", minimum=0, maximum=9_223_372_036_854_775_807)
    end = _normalize_integer_range(end_time, field="end_time", minimum=0, maximum=9_223_372_036_854_775_807)
    if start is not None and end is not None:
        if int(start) > int(end):
            raise ValueError("start_time must be less than or equal to end_time.")
        if maximum_span_ms is not None and int(end) - int(start) > maximum_span_ms:
            raise ValueError(f"start_time and end_time must span no more than {maximum_span_ms} milliseconds.")
    return start, end


def _mapping_items(value: Any, *, resource: str, payload: Any, allow_single: bool = True) -> list[dict[str, Any]]:
    if allow_single and isinstance(value, dict):
        items = [value]
    elif isinstance(value, list):
        items = value
    else:
        raise WeexRestBusinessError(code="invalid_response", message=f"WEEX {resource} response was invalid.", payload=payload)
    if not all(isinstance(item, dict) for item in items):
        raise WeexRestBusinessError(code="invalid_response", message=f"WEEX {resource} response contained an invalid item.", payload=payload)
    return items


def _require_mapping_fields(
    items: list[dict[str, Any]],
    *,
    fields: tuple[str, ...],
    resource: str,
    payload: Any,
) -> None:
    if not all(all(item.get(field) not in (None, "") for field in fields) for item in items):
        raise WeexRestBusinessError(code="invalid_response", message=f"WEEX {resource} response was missing required fields.", payload=payload)


def _require_numeric_fields(
    items: list[dict[str, Any]],
    *,
    fields: tuple[str, ...],
    resource: str,
    payload: Any,
    allow_zero: bool,
    allow_negative: bool = False,
) -> None:
    if not all(
        _valid_number(item.get(field), allow_zero=allow_zero, allow_negative=allow_negative)
        for item in items
        for field in fields
    ):
        raise WeexRestBusinessError(
            code="invalid_response",
            message=f"WEEX {resource} response contained an invalid numeric field.",
            payload=payload,
        )


def _require_integer_fields(
    items: list[dict[str, Any]],
    *,
    fields: tuple[str, ...],
    resource: str,
    payload: Any,
) -> None:
    if not all(
        _valid_integer(item.get(field), allow_zero=True)
        for item in items
        for field in fields
    ):
        raise WeexRestBusinessError(
            code="invalid_response",
            message=f"WEEX {resource} response contained an invalid integer field.",
            payload=payload,
        )


def _require_identifier_fields(
    items: list[dict[str, Any]],
    *,
    fields: tuple[str, ...],
    resource: str,
    payload: Any,
) -> None:
    if not all(
        _valid_identifier(item.get(field))
        for item in items
        for field in fields
    ):
        raise WeexRestBusinessError(
            code="invalid_response",
            message=f"WEEX {resource} response contained an invalid identifier.",
            payload=payload,
        )


def _require_requested_symbol(items: list[dict[str, Any]], requested_symbol: str | None, *, payload: Any) -> None:
    if requested_symbol is None:
        return
    if not items or any(_safe_normalize_response_symbol(item.get("symbol")) != requested_symbol for item in items):
        raise WeexRestBusinessError(code="invalid_response", message="WEEX market response symbol did not match the requested symbol.", payload=payload)


def _safe_normalize_response_symbol(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        return normalize_market_symbol(str(value))
    except ValueError:
        return None


def _valid_integer(value: Any, *, allow_zero: bool) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value >= 0 if allow_zero else value > 0
    if not isinstance(value, str) or not value.isascii() or not value.isdigit():
        return False
    if len(value) > 1 and value.startswith("0"):
        return False
    parsed = int(value)
    return parsed >= 0 if allow_zero else parsed > 0


def _valid_identifier(value: Any) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, int):
        return value >= 0
    return isinstance(value, str) and bool(value.strip())
