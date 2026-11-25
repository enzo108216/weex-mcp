from __future__ import annotations

from typing import Any

from ..adapters.market import MarketPriceAdapter, normalize_market_symbol
from ..adapters.rest_errors import WeexRestBusinessError
from ..response import error_response, ok_response


def get_symbol_price(arguments: dict[str, Any], *, adapter: MarketPriceAdapter) -> dict[str, Any]:
    missing = _missing_fields(arguments, ("market",))
    if missing:
        return error_response("missing_fields", "Market price query is missing required fields.", details={"fields": missing})
    symbol = arguments.get("symbol")
    symbols = arguments.get("symbols")
    has_symbol = symbol not in (None, "")
    has_symbols = symbols is not None
    market = str(arguments["market"]).lower()
    if not has_symbol and not has_symbols and market != "spot":
        return error_response(
            "missing_fields",
            "Market price query is missing required fields.",
            details={"fields": ["symbol", "symbols"]},
        )
    if has_symbol and has_symbols:
        return error_response("invalid_arguments", "symbol and symbols are mutually exclusive.")
    try:
        if has_symbols:
            data = adapter.get_symbol_price(
                market=market,
                symbols=_normalize_symbols(symbols),
                price_type=arguments.get("price_type"),
            )
        elif has_symbol:
            data = adapter.get_symbol_price(
                market=market,
                symbol=normalize_market_symbol(str(symbol)),
                price_type=arguments.get("price_type"),
            )
        else:
            data = adapter.get_symbol_price(
                market=market,
                price_type=arguments.get("price_type"),
            )
    except ValueError as exc:
        return error_response("invalid_arguments", str(exc))
    except WeexRestBusinessError as exc:
        return error_response("upstream_error", "WEEX market API returned an error.", details=exc.safe_details())
    except Exception as exc:
        return error_response(
            "upstream_error",
            "Market adapter failed before returning a price result.",
            details={"reason": exc.__class__.__name__},
        )
    return ok_response(data=data)


def get_depth(arguments: dict[str, Any], *, adapter: MarketPriceAdapter) -> dict[str, Any]:
    missing = _missing_fields(arguments, ("market", "symbol"))
    if missing:
        return error_response("missing_fields", "Market depth query is missing required fields.", details={"fields": missing})
    return _call_market_adapter(
        lambda: adapter.get_depth(
            market=str(arguments["market"]).lower(),
            symbol=normalize_market_symbol(str(arguments["symbol"])),
            limit=arguments.get("limit"),
        )
    )


def get_klines(arguments: dict[str, Any], *, adapter: MarketPriceAdapter) -> dict[str, Any]:
    missing = _missing_fields(arguments, ("market", "symbol", "interval"))
    if missing:
        return error_response("missing_fields", "Market K-line query is missing required fields.", details={"fields": missing})
    market = str(arguments["market"]).lower()
    if market == "spot" and arguments.get("limit") not in (None, ""):
        return error_response("invalid_arguments", "limit is only supported for futures K-line queries.")
    return _call_market_adapter(
        lambda: adapter.get_klines(
            market=market,
            symbol=normalize_market_symbol(str(arguments["symbol"])),
            interval=str(arguments["interval"]),
            limit=arguments.get("limit"),
        )
    )


def get_exchange_info(arguments: dict[str, Any], *, adapter: MarketPriceAdapter) -> dict[str, Any]:
    missing = _missing_fields(arguments, ("market",))
    if missing:
        return error_response("missing_fields", "Market exchange-info query is missing required fields.", details={"fields": missing})
    symbol = arguments.get("symbol")
    symbols = arguments.get("symbols")
    if symbol not in (None, "") and symbols is not None:
        return error_response("invalid_arguments", "symbol and symbols are mutually exclusive.")
    market = str(arguments["market"]).lower()
    if symbols is not None or arguments.get("symbol_status") is not None:
        return _call_market_adapter(
            lambda: adapter.get_exchange_info(
                market=market,
                symbol=normalize_market_symbol(str(symbol)) if symbol else None,
                symbols=_normalize_symbols(symbols) if symbols is not None else None,
                symbol_status=arguments.get("symbol_status"),
            )
        )
    return _call_market_adapter(
        lambda: adapter.get_exchange_info(
            market=market,
            symbol=normalize_market_symbol(str(symbol)) if symbol else None,
        )
    )


def get_server_time(arguments: dict[str, Any], *, adapter: MarketPriceAdapter) -> dict[str, Any]:
    return _call_market_with_fields(
        arguments,
        required=("market",),
        call=lambda: adapter.get_server_time(market=str(arguments["market"]).lower()),
    )


def ping(arguments: dict[str, Any], *, adapter: MarketPriceAdapter) -> dict[str, Any]:
    return _call_market_with_fields(
        arguments,
        required=("market",),
        call=lambda: adapter.ping(market=str(arguments["market"]).lower()),
    )


def get_ticker_24h(arguments: dict[str, Any], *, adapter: MarketPriceAdapter) -> dict[str, Any]:
    symbol = arguments.get("symbol")
    symbols = arguments.get("symbols")
    if symbol not in (None, "") and symbols is not None:
        return error_response("invalid_arguments", "symbol and symbols are mutually exclusive.")
    return _call_market_with_fields(
        arguments,
        required=("market",),
        call=lambda: adapter.get_ticker_24h(
            market=str(arguments["market"]).lower(),
            symbol=_optional_symbol(symbol),
            symbols=_normalize_symbols(symbols) if symbols is not None else None,
        ),
    )


def get_book_ticker(arguments: dict[str, Any], *, adapter: MarketPriceAdapter) -> dict[str, Any]:
    symbol = arguments.get("symbol")
    symbols = arguments.get("symbols")
    if symbol not in (None, "") and symbols is not None:
        return error_response("invalid_arguments", "symbol and symbols are mutually exclusive.")
    return _call_market_with_fields(
        arguments,
        required=("market",),
        call=lambda: adapter.get_book_ticker(
            market=str(arguments["market"]).lower(),
            symbol=_optional_symbol(symbol),
            symbols=_normalize_symbols(symbols) if symbols is not None else None,
        ),
    )


def get_recent_trades(arguments: dict[str, Any], *, adapter: MarketPriceAdapter) -> dict[str, Any]:
    return _call_market_with_fields(
        arguments,
        required=("market", "symbol"),
        call=lambda: adapter.get_recent_trades(
            market=str(arguments["market"]).lower(),
            symbol=normalize_market_symbol(str(arguments["symbol"])),
            limit=arguments.get("limit"),
        ),
    )


def get_funding_rate(arguments: dict[str, Any], *, adapter: MarketPriceAdapter) -> dict[str, Any]:
    return _call_market_with_fields(
        arguments,
        required=("market", "mode"),
        call=lambda: adapter.get_funding_rate(
            market=str(arguments["market"]).lower(),
            mode=str(arguments["mode"]).lower(),
            symbol=_optional_symbol(arguments.get("symbol")),
            start_time=arguments.get("start_time"),
            end_time=arguments.get("end_time"),
            limit=arguments.get("limit"),
        ),
    )


def get_open_interest(arguments: dict[str, Any], *, adapter: MarketPriceAdapter) -> dict[str, Any]:
    return _call_market_with_fields(
        arguments,
        required=("market", "symbol"),
        call=lambda: adapter.get_open_interest(
            market=str(arguments["market"]).lower(),
            symbol=normalize_market_symbol(str(arguments["symbol"])),
        ),
    )


def get_advanced_klines(arguments: dict[str, Any], *, adapter: MarketPriceAdapter) -> dict[str, Any]:
    return _call_market_with_fields(
        arguments,
        required=("market", "kind", "symbol", "interval"),
        call=lambda: adapter.get_advanced_klines(
            market=str(arguments["market"]).lower(),
            kind=str(arguments["kind"]).lower(),
            symbol=normalize_market_symbol(str(arguments["symbol"])),
            interval=str(arguments["interval"]),
            start_time=arguments.get("start_time"),
            end_time=arguments.get("end_time"),
            limit=arguments.get("limit"),
            price_type=arguments.get("price_type"),
        ),
    )


def get_catalog(arguments: dict[str, Any], *, adapter: MarketPriceAdapter) -> dict[str, Any]:
    return _call_market_with_fields(
        arguments,
        required=("market", "catalog"),
        call=lambda: adapter.get_catalog(
            market=str(arguments["market"]).lower(),
            catalog=str(arguments["catalog"]).lower(),
        ),
    )


def _call_market_with_fields(arguments: dict[str, Any], *, required: tuple[str, ...], call: Any) -> dict[str, Any]:
    missing = _missing_fields(arguments, required)
    if missing:
        return error_response("missing_fields", "Market query is missing required fields.", details={"fields": missing})
    return _call_market_adapter(call)


def _optional_symbol(value: Any) -> str | None:
    return normalize_market_symbol(str(value)) if value not in (None, "") else None


def _normalize_symbols(value: Any) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError("symbols must be a non-empty array.")
    if not all(isinstance(symbol, str) for symbol in value):
        raise ValueError("symbols must contain only strings.")
    normalized = [normalize_market_symbol(symbol) for symbol in value]
    if len(set(normalized)) != len(normalized):
        raise ValueError("symbols must contain unique symbols.")
    return normalized


def _call_market_adapter(call: Any) -> dict[str, Any]:
    try:
        return ok_response(data=call())
    except ValueError as exc:
        return error_response("invalid_arguments", str(exc))
    except WeexRestBusinessError as exc:
        return error_response("upstream_error", "WEEX market API returned an error.", details=exc.safe_details())
    except Exception as exc:
        return error_response(
            "upstream_error",
            "Market adapter failed before returning a result.",
            details={"reason": exc.__class__.__name__},
        )


def _missing_fields(arguments: dict[str, Any], fields: tuple[str, ...]) -> list[str]:
    return [field for field in fields if not arguments.get(field)]
