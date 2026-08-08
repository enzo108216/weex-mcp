from __future__ import annotations

from typing import Any

from ..adapters.market import MarketPriceAdapter, normalize_market_symbol
from ..adapters.rest_errors import WeexRestBusinessError
from ..response import error_response, ok_response


def get_symbol_price(arguments: dict[str, Any], *, adapter: MarketPriceAdapter) -> dict[str, Any]:
    missing = _missing_fields(arguments, ("market", "symbol"))
    if missing:
        return error_response("missing_fields", "Market price query is missing required fields.", details={"fields": missing})
    market = arguments["market"]
    symbol = arguments["symbol"]
    try:
        data = adapter.get_symbol_price(
            market=str(market).lower(),
            symbol=normalize_market_symbol(str(symbol)),
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
    return _call_market_adapter(
        lambda: adapter.get_exchange_info(
            market=str(arguments["market"]).lower(),
            symbol=normalize_market_symbol(str(symbol)) if symbol else None,
        )
    )


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
