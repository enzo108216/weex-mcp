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

DEPTH_LIMITS = {"15", "200"}
SPOT_KLINE_INTERVALS = {"1m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "1w"}
FUTURES_KLINE_INTERVALS = {"1m", "5m", "15m", "30m", "1h", "4h", "12h", "1d", "1w"}


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


def _valid_number(value: Any, *, allow_zero: bool) -> bool:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return False
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return False
    if not number.is_finite():
        return False
    return number >= 0 if allow_zero else number > 0
