from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ..secret_provider import ResolvedWeexCredential, SecretProvider, SecretProviderError
from ..tenant_context import TenantContext
from ..trade_execution import TradeExecution, canonical_payload_hash
from .market import MarketPriceAdapter, WeexMarketRestAdapter
from .rest_errors import SUCCESS_CODES, WeexRestBusinessError, raise_for_weex_business_error

SPOT_BASE_URL = "https://api-spot.weex.com"
FUTURES_BASE_URL = "https://api-contract.weex.com"
SPOT_ORDER_PATH = "/api/v3/order"
FUTURES_ORDER_PATH = "/capi/v3/order"
FUTURES_DEMO_ORDER_PATH = "/capi/v3/sim/order"
FUTURES_LEVERAGE_PATH = "/capi/v3/account/leverage"
FUTURES_MARGIN_MODE_PATH = "/capi/v3/account/marginType"
FUTURES_SYMBOL_CONFIG_PATH = "/capi/v3/account/symbolConfig"
SPOT_ACCOUNT_CONFIG_PATH = "/api/v3/account"
FUTURES_ACCOUNT_CONFIG_PATH = "/capi/v3/account/accountConfig"
SPOT_USDT_MIN_ORDER_VALUE = Decimal("5")

PreparedTransport = Callable[[dict[str, Any], float], bytes]


class WeexTradeRestAdapter:
    """Signed WEEX trade adapter for spot and futures order operations.

    The adapter never stores resolved credentials. Preview resolves the injected
    provider only to validate the request and permission; confirmation resolves
    it again immediately before the signed exchange request.
    """

    def __init__(
        self,
        *,
        secret_provider: SecretProvider,
        spot_base_url: str = SPOT_BASE_URL,
        futures_base_url: str = FUTURES_BASE_URL,
        timeout_seconds: float = 5.0,
        transport: PreparedTransport | None = None,
        time_func: Callable[[], float] | None = None,
        market_adapter: MarketPriceAdapter | None = None,
    ):
        self.secret_provider = secret_provider
        self.spot_base_url = spot_base_url.rstrip("/")
        self.futures_base_url = futures_base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.transport = transport or _default_transport
        self.time_func = time_func or time.time
        self.market_adapter = market_adapter or WeexMarketRestAdapter(
            spot_base_url=self.spot_base_url,
            futures_base_url=self.futures_base_url,
            timeout_seconds=timeout_seconds,
        )

    def preview_order(
        self,
        *,
        arguments: dict[str, Any],
        credential: Any,
        credential_argument: dict[str, Any],
        context: TenantContext,
    ) -> dict[str, Any]:
        self._credential_for_request(credential, credential_argument, context)
        market, trading_mode = _normalize_market_mode(arguments)
        if market == "spot" and trading_mode == "demo":
            raise ValueError("Spot demo orders are not supported by the WEEX API catalog.")
        body = _build_order_body(arguments, market=market, trading_mode=trading_mode)
        catalog_symbol = _normalize_catalog_symbol(
            arguments.get("symbol"),
            market=market,
            trading_mode=trading_mode,
        )
        symbol_rule = _find_symbol_rule(
            self.market_adapter.get_exchange_info(market=market, symbol=catalog_symbol),
            symbol=catalog_symbol,
            market=market,
        )
        _validate_order_rules(body, symbol_rule, market=market)
        endpoint = _order_path(market=market, trading_mode=trading_mode)
        return _preview(
            action="order",
            market=market,
            trading_mode=trading_mode,
            endpoint=endpoint,
            body=body,
            risk_alerts=["live_order_requires_confirmation"]
            if trading_mode == "live"
            else ["demo_futures_order", "Demo futures orders are not submitted to the live market."],
        )

    def confirm_order(
        self,
        *,
        execution: TradeExecution,
        credential: Any,
        credential_argument: dict[str, Any],
        context: TenantContext,
    ) -> dict[str, Any]:
        if execution.summary.get("action") not in (None, "order"):
            raise ValueError("Execution action is not an order.")
        body = dict(execution.summary.get("request_body") or {})
        endpoint = str(execution.summary.get("endpoint") or "")
        _validate_execution_order(execution.market, execution.trading_mode, endpoint, body)
        credential = self._credential_for_request(credential, credential_argument, context)
        payload = self._request(
            market=execution.market,
            method="POST",
            path=endpoint,
            query={},
            body=body,
            credential=credential,
        )
        result_payload = _extract_mapping_payload(payload)
        _raise_if_trade_rejected(payload, result_payload)
        if execution.market == "futures" and not (
            (isinstance(payload, dict) and payload.get("success") is True)
            or result_payload.get("success") is True
        ):
            raise WeexRestBusinessError(
                code="invalid_response",
                message="WEEX futures order response did not explicitly confirm success.",
                payload=payload,
            )
        order_id = _optional_str(result_payload.get("orderId"))
        if not order_id:
            raise WeexRestBusinessError(code="invalid_response", message="WEEX order response did not contain orderId.", payload=payload)
        return {
            "exchange_order_id": order_id,
            "client_order_id": _optional_str(result_payload.get("clientOrderId") or body.get("newClientOrderId")),
            "status": "accepted",
            "success": True,
            "raw": payload,
        }

    def preview_cancel_order(
        self,
        *,
        arguments: dict[str, Any],
        credential: Any,
        credential_argument: dict[str, Any],
        context: TenantContext,
    ) -> dict[str, Any]:
        self._credential_for_request(credential, credential_argument, context)
        market, trading_mode = _normalize_market_mode(arguments)
        if trading_mode != "live":
            raise ValueError("WEEX cancel endpoints only support live trading_mode.")
        query: dict[str, str] = {}
        if arguments.get("order_id"):
            query["orderId"] = _normalize_order_id(arguments["order_id"])
        if arguments.get("client_order_id"):
            query["origClientOrderId"] = _normalize_client_order_id(arguments["client_order_id"])
        if not query:
            raise ValueError("order_id or client_order_id is required.")
        return _preview(
            action="cancel_order",
            market=market,
            trading_mode=trading_mode,
            endpoint=_order_path(market=market, trading_mode=trading_mode),
            query=query,
            risk_alerts=["live_cancel_requires_confirmation"],
        )

    def confirm_cancel_order(
        self,
        *,
        execution: TradeExecution,
        credential: Any,
        credential_argument: dict[str, Any],
        context: TenantContext,
    ) -> dict[str, Any]:
        if execution.summary.get("action") != "cancel_order":
            raise ValueError("Execution action is not a cancel_order.")
        path = str(execution.summary.get("endpoint") or "")
        query = {str(k): str(v) for k, v in dict(execution.summary.get("request_query") or {}).items()}
        if path not in {SPOT_ORDER_PATH, FUTURES_ORDER_PATH} or execution.trading_mode != "live":
            raise ValueError("Execution endpoint is not a supported live cancel endpoint.")
        credential = self._credential_for_request(credential, credential_argument, context)
        payload = self._request(market=execution.market, method="DELETE", path=path, query=query, body={}, credential=credential)
        result_payload = _extract_mapping_payload(payload)
        _raise_if_trade_rejected(payload, result_payload)
        status = str(result_payload.get("status") or "").upper()
        if execution.market == "futures":
            success = result_payload.get("success") is True
        else:
            success = status in {"CANCELED", "CANCELLED"} and result_payload.get("orderId") not in (None, "")
        if not success:
            raise WeexRestBusinessError(code="invalid_response", message="WEEX cancel response did not confirm cancellation.", payload=payload)
        return {
            "exchange_order_id": _optional_str(result_payload.get("orderId")),
            "client_order_id": _optional_str(result_payload.get("origClientOrderId")),
            "status": "canceled",
            "success": True,
            "raw": payload,
        }

    def preview_leverage(
        self,
        *,
        arguments: dict[str, Any],
        credential: Any,
        credential_argument: dict[str, Any],
        context: TenantContext,
    ) -> dict[str, Any]:
        resolved_credential = self._credential_for_request(credential, credential_argument, context)
        market, trading_mode = _normalize_market_mode(arguments)
        if market != "futures" or trading_mode != "live":
            raise ValueError("Leverage endpoint only supports live futures.")
        symbol = _normalize_symbol(arguments.get("symbol"))
        leverage = _positive_decimal(arguments.get("leverage"), "leverage")
        symbol_rule = _find_symbol_rule(
            self.market_adapter.get_exchange_info(market="futures", symbol=symbol),
            symbol=symbol,
            market="futures",
        )
        _validate_leverage_rule(leverage, symbol_rule)
        margin_mode = _normalize_margin_mode(arguments.get("margin_mode"))
        current_leverages = _extract_symbol_leverages(
            self._request(
                market="futures",
                method="GET",
                path=FUTURES_SYMBOL_CONFIG_PATH,
                query={"symbol": symbol},
                body={},
                credential=resolved_credential,
            ),
            symbol=symbol,
        )
        body: dict[str, str] = {
            "symbol": symbol,
            "marginType": margin_mode,
            **current_leverages,
        }
        position_side = str(arguments.get("position_side") or "").strip().upper()
        if margin_mode == "CROSSED":
            # WEEX separated-position accounts reject cross updates unless all
            # three leverage fields are populated with the requested value.
            # The two isolated values are compatibility fillers and are not
            # applied while marginType is CROSSED.
            for field in ("crossLeverage", "isolatedLongLeverage", "isolatedShortLeverage"):
                body[field] = leverage
        elif position_side == "LONG":
            body["isolatedLongLeverage"] = leverage
        elif position_side == "SHORT":
            body["isolatedShortLeverage"] = leverage
        else:
            raise ValueError("position_side must be LONG or SHORT when margin_mode=isolated.")
        return _preview(action="update_leverage", market=market, trading_mode=trading_mode, endpoint=FUTURES_LEVERAGE_PATH, body=body, risk_alerts=["leverage_change_requires_confirmation"])

    def confirm_leverage(self, *, execution: TradeExecution, credential: Any, credential_argument: dict[str, Any], context: TenantContext) -> dict[str, Any]:
        return self._confirm_mutation(execution=execution, expected_action="update_leverage", endpoint=FUTURES_LEVERAGE_PATH, credential=credential, credential_argument=credential_argument, context=context)

    def preview_margin_mode(
        self,
        *,
        arguments: dict[str, Any],
        credential: Any,
        credential_argument: dict[str, Any],
        context: TenantContext,
    ) -> dict[str, Any]:
        self._credential_for_request(credential, credential_argument, context)
        market, trading_mode = _normalize_market_mode(arguments)
        if market != "futures" or trading_mode != "live":
            raise ValueError("Margin mode endpoint only supports live futures.")
        symbol = _normalize_symbol(arguments.get("symbol"))
        _find_symbol_rule(
            self.market_adapter.get_exchange_info(market="futures", symbol=symbol),
            symbol=symbol,
            market="futures",
        )
        body = {"symbol": symbol, "marginType": _normalize_margin_mode(arguments.get("margin_mode"))}
        if arguments.get("separated_type"):
            separated = str(arguments["separated_type"]).strip().upper()
            if separated not in {"COMBINED", "SEPARATED"}:
                raise ValueError("separated_type must be COMBINED or SEPARATED.")
            body["separatedType"] = separated
        return _preview(action="change_margin_mode", market=market, trading_mode=trading_mode, endpoint=FUTURES_MARGIN_MODE_PATH, body=body, risk_alerts=["margin_mode_change_requires_confirmation"])

    def confirm_margin_mode(self, *, execution: TradeExecution, credential: Any, credential_argument: dict[str, Any], context: TenantContext) -> dict[str, Any]:
        return self._confirm_mutation(execution=execution, expected_action="change_margin_mode", endpoint=FUTURES_MARGIN_MODE_PATH, credential=credential, credential_argument=credential_argument, context=context)

    def _confirm_mutation(self, *, execution: TradeExecution, expected_action: str, endpoint: str, credential: Any, credential_argument: dict[str, Any], context: TenantContext) -> dict[str, Any]:
        if execution.summary.get("action") != expected_action or execution.summary.get("endpoint") != endpoint or execution.market != "futures" or execution.trading_mode != "live":
            raise ValueError("Execution does not match the requested trade mutation.")
        credential = self._credential_for_request(credential, credential_argument, context)
        payload = self._request(market="futures", method="POST", path=endpoint, query={}, body=dict(execution.summary.get("request_body") or {}), credential=credential)
        result_payload = _extract_mapping_payload(payload)
        _raise_if_trade_rejected(payload, result_payload)
        if endpoint == FUTURES_LEVERAGE_PATH:
            evidence = _leverage_response_matches_request(
                request_body=dict(execution.summary.get("request_body") or {}),
                response_payload=result_payload,
            )
        else:
            evidence = _has_success_code(payload, endpoint=endpoint) or result_payload.get("success") is True
        if not evidence:
            raise WeexRestBusinessError(code="invalid_response", message="WEEX trade mutation response was malformed.", payload=payload)
        return {"status": "accepted", "success": True, "raw": payload}

    def _resolve_trade_credential(self, credential_argument: dict[str, Any], context: TenantContext) -> Any:
        return self.secret_provider.resolve(credential_argument=credential_argument, context=context, required_permission="TRADE")

    def resolve_credential(self, *, credential_argument: dict[str, Any], context: TenantContext) -> ResolvedWeexCredential:
        return self._resolve_trade_credential(credential_argument, context)

    def validate_trade_access(
        self,
        *,
        arguments: dict[str, Any],
        credential: ResolvedWeexCredential,
        context: TenantContext,
    ) -> ResolvedWeexCredential:
        """Authenticate the request key through a read-only endpoint before preview."""

        market, _ = _normalize_market_mode(arguments)
        path = SPOT_ACCOUNT_CONFIG_PATH if market == "spot" else FUTURES_ACCOUNT_CONFIG_PATH
        payload = self._request(
            market=market,
            method="GET",
            path=path,
            query={},
            body={},
            credential=credential,
        )
        account_config = _extract_mapping_payload(payload)
        can_trade = account_config.get("canTrade")
        if can_trade is False:
            raise SecretProviderError("permission_denied", "WEEX API key does not allow trading.")
        if can_trade is not True:
            raise WeexRestBusinessError(
                code="invalid_response",
                message="WEEX account configuration did not contain a boolean canTrade value.",
                payload=payload,
            )
        return replace(
            credential,
            permissions=credential.permissions | frozenset({"USER_DATA", "TRADE"}),
        )

    def _credential_for_request(self, credential: Any, credential_argument: dict[str, Any], context: TenantContext) -> Any:
        if isinstance(credential, ResolvedWeexCredential):
            return credential
        return self._resolve_trade_credential(credential_argument, context)

    def _request(self, *, market: str, method: str, path: str, query: dict[str, str], body: dict[str, Any], credential: Any) -> Any:
        prepared = _prepare_signed_request(
            base_url=self.spot_base_url if market == "spot" else self.futures_base_url,
            method=method,
            path=path,
            query=query,
            body=body,
            api_key=credential.api_key,
            api_secret=credential.api_secret,
            api_passphrase=credential.api_passphrase,
            timestamp_ms=str(int(self.time_func() * 1000)),
        )
        raw_bytes = self.transport(prepared, self.timeout_seconds)
        try:
            payload = json.loads(raw_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WeexRestBusinessError(code="invalid_response", message="WEEX returned malformed JSON.") from exc
        validation_payload = payload
        if (
            path == FUTURES_MARGIN_MODE_PATH
            and isinstance(payload, dict)
            and str(payload.get("code") or "") == "200"
        ):
            # This endpoint documents HTTP-style ``200`` as its business
            # success code. Normalize only the outer envelope so nested
            # ``success=false`` or error codes are still scanned fail-closed.
            validation_payload = dict(payload)
            validation_payload["code"] = "0"
        raise_for_weex_business_error(validation_payload)
        return payload


class WeexFuturesTradeRestAdapter(WeexTradeRestAdapter):
    """Compatibility adapter limited to futures demo trading."""

    def preview_order(self, *, arguments: dict[str, Any], credential: Any, credential_argument: dict[str, Any], context: TenantContext) -> dict[str, Any]:
        if str(arguments.get("market") or "").lower() != "futures" or str(arguments.get("trading_mode") or "").lower() != "demo":
            raise ValueError("WeexFuturesTradeRestAdapter only supports demo futures orders.")
        return super().preview_order(arguments=arguments, credential=credential, credential_argument=credential_argument, context=context)

    def confirm_order(self, *, execution: TradeExecution, credential: Any, credential_argument: dict[str, Any], context: TenantContext) -> dict[str, Any]:
        if execution.market != "futures" or execution.trading_mode != "demo":
            raise ValueError("WeexFuturesTradeRestAdapter only supports demo futures orders.")
        return super().confirm_order(execution=execution, credential=credential, credential_argument=credential_argument, context=context)

    def preview_cancel_order(self, *, arguments: dict[str, Any], credential: Any, credential_argument: dict[str, Any], context: TenantContext) -> dict[str, Any]:
        raise ValueError("WeexFuturesTradeRestAdapter does not support cancel mutations.")

    def confirm_cancel_order(self, *, execution: TradeExecution, credential: Any, credential_argument: dict[str, Any], context: TenantContext) -> dict[str, Any]:
        raise ValueError("WeexFuturesTradeRestAdapter does not support cancel mutations.")

    def preview_leverage(self, *, arguments: dict[str, Any], credential: Any, credential_argument: dict[str, Any], context: TenantContext) -> dict[str, Any]:
        raise ValueError("WeexFuturesTradeRestAdapter does not support leverage mutations.")

    def confirm_leverage(self, *, execution: TradeExecution, credential: Any, credential_argument: dict[str, Any], context: TenantContext) -> dict[str, Any]:
        raise ValueError("WeexFuturesTradeRestAdapter does not support leverage mutations.")

    def preview_margin_mode(self, *, arguments: dict[str, Any], credential: Any, credential_argument: dict[str, Any], context: TenantContext) -> dict[str, Any]:
        raise ValueError("WeexFuturesTradeRestAdapter does not support margin-mode mutations.")

    def confirm_margin_mode(self, *, execution: TradeExecution, credential: Any, credential_argument: dict[str, Any], context: TenantContext) -> dict[str, Any]:
        raise ValueError("WeexFuturesTradeRestAdapter does not support margin-mode mutations.")


def _preview(*, action: str, market: str, trading_mode: str, endpoint: str, body: dict[str, Any] | None = None, query: dict[str, str] | None = None, risk_alerts: list[str]) -> dict[str, Any]:
    summary: dict[str, Any] = {"action": action, "market": market, "trading_mode": trading_mode, "endpoint": endpoint}
    if body:
        summary["request_body"] = body
        if "symbol" in body:
            summary["symbol"] = body["symbol"]
        for source, target in (
            ("side", "side"),
            ("positionSide", "position_side"),
            ("type", "order_type"),
            ("quantity", "quantity"),
            ("newClientOrderId", "client_order_id"),
        ):
            if source in body:
                summary[target] = body[source]
    if query:
        summary["request_query"] = query
    signature_prefix = "demo_order" if action == "order" and trading_mode == "demo" else action
    signature = f"{signature_prefix}_" + canonical_payload_hash({"endpoint": endpoint, "body": body or {}, "query": query or {}})[:24]
    return {"risk_signature": signature, "summary": summary, "risk": {"status": "review_required", "alerts": risk_alerts}}


def _find_symbol_rule(exchange_info: dict[str, Any], *, symbol: str, market: str) -> dict[str, Any]:
    symbols = exchange_info.get("symbols") if isinstance(exchange_info, dict) else None
    if not isinstance(symbols, list):
        raise ValueError("Exchange information did not contain symbol rules.")
    for item in symbols:
        if isinstance(item, dict) and str(item.get("symbol") or "").strip().upper() == symbol:
            if market == "spot":
                status = item.get("status")
                if not isinstance(status, str) or not status.strip():
                    raise WeexRestBusinessError(code="invalid_response", message="Exchange information is missing symbol status.")
                if status.strip().upper() != "TRADING" or not isinstance(item.get("enableTrade"), bool):
                    if item.get("enableTrade") is not False:
                        raise WeexRestBusinessError(code="invalid_response", message="Exchange information is missing enableTrade.")
                    raise ValueError("Symbol is not currently tradable.")
                if item["enableTrade"] is False:
                    raise ValueError("Symbol is not currently tradable.")
            else:
                status = str(item.get("status") or "").strip().upper()
                if status and status != "TRADING":
                    raise ValueError("Symbol is not currently tradable.")
                if item.get("enableTrade") is False:
                    raise ValueError("Symbol is not currently tradable.")
            return item
    raise ValueError("Symbol is not listed in WEEX exchange information.")


def _validate_order_rules(body: dict[str, Any], rule: dict[str, Any], *, market: str) -> None:
    _validate_symbol_rule_shape(rule, market=market, order_type=str(body.get("type") or "").upper())
    try:
        quantity = Decimal(str(body["quantity"]))
    except (InvalidOperation, KeyError, ValueError):
        raise ValueError("Order quantity is invalid.") from None

    minimum = rule.get("minTradeAmount")
    if minimum in (None, ""):
        minimum = rule.get("minOrderSize")
    if minimum in (None, ""):
        minimum_quantity = None
    else:
        try:
            minimum_quantity = Decimal(str(minimum))
        except (InvalidOperation, ValueError):
            raise ValueError("Exchange information contained an invalid minimum order quantity.") from None
    if minimum_quantity is not None and quantity < minimum_quantity:
        raise ValueError("quantity is below the WEEX minimum order size for this symbol.")

    maximum = rule.get("maxTradeAmount" if market == "spot" else "maxOrderSize")
    if maximum not in (None, ""):
        try:
            maximum_quantity = Decimal(str(maximum))
        except (InvalidOperation, ValueError):
            raise ValueError("Exchange information contained an invalid maximum order quantity.") from None
        if not maximum_quantity.is_finite() or maximum_quantity <= 0:
            raise ValueError("Exchange information contained an invalid maximum order quantity.")
        if quantity > maximum_quantity:
            raise ValueError("quantity exceeds the WEEX maximum order size for this symbol.")

    step_size = rule.get("stepSize")
    if step_size not in (None, ""):
        try:
            step = Decimal(str(step_size))
        except (InvalidOperation, ValueError):
            raise ValueError("Exchange information contained an invalid quantity step size.") from None
        if step > 0 and quantity % step != 0:
            raise ValueError("quantity does not align with the WEEX step size for this symbol.")

    quantity_precision = rule.get("quantityPrecision")
    if quantity_precision not in (None, ""):
        try:
            precision = int(quantity_precision)
            quantum = Decimal(1).scaleb(-precision)
        except (ValueError, TypeError):
            raise ValueError("Exchange information contained an invalid quantity precision.") from None
        if precision < 0 or quantity != quantity.quantize(quantum):
            raise ValueError("quantity exceeds the WEEX precision for this symbol.")

    price_value = body.get("price")
    if (
        market == "spot"
        and str(rule.get("quoteAsset") or "").upper() == "USDT"
        and price_value not in (None, "")
    ):
        try:
            order_value = quantity * Decimal(str(price_value))
        except (InvalidOperation, ValueError):
            raise ValueError("Order price is invalid.") from None
        if order_value < SPOT_USDT_MIN_ORDER_VALUE:
            raise ValueError("spot USDT order value must be at least 5 USDT.")
    tick_size = rule.get("tickSize")
    if price_value not in (None, "") and tick_size not in (None, ""):
        try:
            price = Decimal(str(price_value))
            tick = Decimal(str(tick_size))
        except (InvalidOperation, ValueError):
            raise ValueError("Exchange information contained an invalid price tick size.") from None
        if tick > 0 and price % tick != 0:
            raise ValueError("price does not align with the WEEX tick size for this symbol.")

    price_precision = rule.get("pricePrecision")
    if price_value not in (None, "") and price_precision not in (None, ""):
        try:
            price = Decimal(str(price_value))
            precision = int(price_precision)
            quantum = Decimal(1).scaleb(-precision)
        except (InvalidOperation, ValueError, TypeError):
            raise ValueError("Exchange information contained an invalid price precision.") from None
        if precision < 0 or price != price.quantize(quantum):
            raise ValueError("price exceeds the WEEX precision for this symbol.")

    for field in ("tpTriggerPrice", "slTriggerPrice"):
        if field in body:
            _validate_trigger_price(body[field], field, rule)


def _validate_trigger_price(value: Any, field: str, rule: dict[str, Any]) -> None:
    """Apply the symbol's regular price precision to TP/SL trigger prices."""
    tick_size = rule.get("tickSize")
    price_precision = rule.get("pricePrecision")
    if tick_size in (None, "") and price_precision in (None, ""):
        raise WeexRestBusinessError(code="invalid_response", message="Exchange information is missing trigger-price rules.")
    try:
        price = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{field} must be a positive number.") from None
    if not price.is_finite() or price <= 0:
        raise ValueError(f"{field} must be a positive number.")
    if tick_size not in (None, ""):
        try:
            tick = Decimal(str(tick_size))
        except (InvalidOperation, ValueError):
            raise ValueError("Exchange information contained an invalid price tick size.") from None
        if tick <= 0 or price % tick != 0:
            raise ValueError(f"{field} does not align with the WEEX tick size for this symbol.")
    if price_precision not in (None, ""):
        try:
            precision = int(price_precision)
            quantum = Decimal(1).scaleb(-precision)
        except (InvalidOperation, ValueError, TypeError):
            raise ValueError("Exchange information contained an invalid price precision.") from None
        if precision < 0 or price != price.quantize(quantum):
            raise ValueError(f"{field} exceeds the WEEX precision for this symbol.")


def _validate_symbol_rule_shape(rule: dict[str, Any], *, market: str, order_type: str | None) -> None:
    """Reject incomplete exchange catalogs instead of validating fail-open."""
    if market == "spot":
        status = rule.get("status")
        if not isinstance(status, str) or not status.strip():
            raise WeexRestBusinessError(code="invalid_response", message="Exchange information is missing symbol status.")
        if not isinstance(rule.get("enableTrade"), bool):
            raise WeexRestBusinessError(code="invalid_response", message="Exchange information is missing enableTrade.")
    if rule.get("minTradeAmount") in (None, "") and rule.get("minOrderSize") in (None, ""):
        raise WeexRestBusinessError(code="invalid_response", message="Exchange information is missing minimum order quantity.")
    if rule.get("stepSize") in (None, "") and rule.get("quantityPrecision") in (None, ""):
        raise WeexRestBusinessError(code="invalid_response", message="Exchange information is missing quantity rules.")
    if order_type == "LIMIT" and rule.get("tickSize") in (None, "") and rule.get("pricePrecision") in (None, ""):
        raise WeexRestBusinessError(code="invalid_response", message="Exchange information is missing price rules.")


def _validate_leverage_rule(leverage_value: str, rule: dict[str, Any]) -> None:
    minimum_raw = rule.get("minLeverage")
    maximum_raw = rule.get("maxLeverage")
    if minimum_raw in (None, "") or maximum_raw in (None, ""):
        raise WeexRestBusinessError(code="invalid_response", message="Exchange information is missing leverage rules.")
    try:
        leverage = Decimal(leverage_value)
        minimum = Decimal(str(minimum_raw))
        maximum = Decimal(str(maximum_raw))
    except (InvalidOperation, ValueError):
        raise WeexRestBusinessError(code="invalid_response", message="Exchange information contained invalid leverage rules.") from None
    if not minimum.is_finite() or not maximum.is_finite() or minimum <= 0 or maximum < minimum:
        raise WeexRestBusinessError(code="invalid_response", message="Exchange information contained invalid leverage rules.")
    if leverage < minimum or leverage > maximum:
        raise ValueError(f"leverage must be between {minimum_raw} and {maximum_raw} for this symbol.")


def _build_order_body(arguments: dict[str, Any], *, market: str, trading_mode: str) -> dict[str, Any]:
    symbol = _normalize_demo_symbol(str(arguments.get("symbol") or "")) if market == "futures" and trading_mode == "demo" else _normalize_symbol(arguments.get("symbol"))
    side = _normalize_enum(arguments.get("side"), {"BUY", "SELL"}, "side")
    order_type = _normalize_enum(arguments.get("order_type"), {"MARKET", "LIMIT"}, "order_type")
    body: dict[str, Any] = {"symbol": symbol, "side": side, "type": order_type, "quantity": _positive_decimal(arguments.get("quantity"), "quantity"), "newClientOrderId": _normalize_client_order_id(arguments.get("client_order_id"))}
    if market == "futures":
        body["positionSide"] = _normalize_enum(arguments.get("position_side"), {"LONG", "SHORT"}, "position_side")
    elif arguments.get("position_side") not in (None, ""):
        raise ValueError("position_side is only supported for futures orders.")
    if order_type == "LIMIT":
        body["price"] = _positive_decimal(arguments.get("price"), "price")
        body["timeInForce"] = _normalize_enum(arguments.get("time_in_force"), {"GTC", "IOC", "FOK", "POST_ONLY"} if market == "futures" else {"GTC", "IOC", "FOK"}, "time_in_force")
    elif arguments.get("price") or arguments.get("time_in_force"):
        raise ValueError("price and time_in_force must be omitted when order_type=MARKET.")
    trigger_pairs = (
        ("tp_trigger_price", "tp_working_type", "tpTriggerPrice", "TpWorkingType"),
        ("sl_trigger_price", "sl_working_type", "slTriggerPrice", "SlWorkingType"),
    )
    for trigger_source, working_source, trigger_target, working_target in trigger_pairs:
        trigger = arguments.get(trigger_source)
        working_type = arguments.get(working_source)
        if trigger not in (None, "") or working_type not in (None, ""):
            if market != "futures":
                raise ValueError("TP/SL trigger fields are only supported for futures orders.")
            if trigger in (None, ""):
                raise ValueError(f"{working_source} requires {trigger_source}.")
            body[trigger_target] = _positive_decimal(trigger, trigger_source)
            if working_type not in (None, ""):
                body[working_target] = _normalize_enum(working_type, {"MARK_PRICE", "CONTRACT_PRICE"}, working_source)
    return body


def _validate_execution_order(market: str, trading_mode: str, endpoint: str, body: dict[str, Any]) -> None:
    expected = _order_path(market=market, trading_mode=trading_mode)
    if endpoint != expected:
        raise ValueError("Preview endpoint is not a supported order endpoint.")
    _build_order_body(
        {
            "market": market,
            "trading_mode": trading_mode,
            "symbol": body.get("symbol"),
            "side": body.get("side"),
            "position_side": body.get("positionSide"),
            "order_type": body.get("type"),
            "quantity": body.get("quantity"),
            "price": body.get("price"),
            "time_in_force": body.get("timeInForce"),
            "client_order_id": body.get("newClientOrderId"),
            "tp_trigger_price": body.get("tpTriggerPrice"),
            "sl_trigger_price": body.get("slTriggerPrice"),
            "tp_working_type": body.get("TpWorkingType"),
            "sl_working_type": body.get("SlWorkingType"),
        },
        market=market,
        trading_mode=trading_mode,
    )


def _normalize_market_mode(arguments: dict[str, Any]) -> tuple[str, str]:
    market = str(arguments.get("market") or "").strip().lower()
    if market == "contract":
        market = "futures"
    mode = str(arguments.get("trading_mode") or "").strip().lower()
    if market not in {"spot", "futures"}:
        raise ValueError("Unsupported market.")
    if mode not in {"live", "demo"}:
        raise ValueError("Unsupported trading_mode.")
    return market, mode


def _order_path(*, market: str, trading_mode: str) -> str:
    if market == "spot":
        return SPOT_ORDER_PATH
    return FUTURES_DEMO_ORDER_PATH if trading_mode == "demo" else FUTURES_ORDER_PATH


def _normalize_symbol(value: Any) -> str:
    normalized = str(value or "").strip().upper().replace("-", "").replace("/", "").replace(" ", "").replace("_", "")
    if not normalized or not normalized.isalnum():
        raise ValueError("Unsupported symbol format.")
    return normalized


def _normalize_catalog_symbol(value: Any, *, market: str, trading_mode: str) -> str:
    normalized = _normalize_symbol(value)
    if market == "futures" and trading_mode == "demo" and normalized.endswith("SUSDT") and len(normalized) > 5:
        return f"{normalized[:-5]}USDT"
    return normalized


def _normalize_demo_symbol(symbol: str) -> str:
    normalized = _normalize_symbol(symbol)
    if normalized.startswith("CMT") and normalized.endswith("USDT"):
        normalized = normalized[3:]
    if normalized.endswith("SUSDT") and len(normalized) > 5:
        return normalized
    if normalized.endswith("USDT") and len(normalized) > 4:
        return f"{normalized[:-4]}SUSDT"
    raise ValueError("Unsupported futures demo symbol format.")


def _normalize_enum(value: Any, allowed: set[str], field: str) -> str:
    normalized = str(value or "").strip().upper()
    if normalized not in allowed:
        raise ValueError(f"{field} must be one of {', '.join(sorted(allowed))}.")
    return normalized


def _normalize_margin_mode(value: Any) -> str:
    normalized = str(value or "").strip().upper()
    if normalized == "CROSS":
        normalized = "CROSSED"
    if normalized not in {"CROSSED", "ISOLATED"}:
        raise ValueError("margin_mode must be cross or isolated.")
    return normalized


_CLIENT_ORDER_ID_RE = re.compile(r"^[.A-Za-z0-9_:/-]{1,36}$")
_ORDER_ID_RE = re.compile(r"^[0-9]+$")


def _normalize_order_id(value: Any) -> str:
    normalized = str(value or "").strip()
    if not normalized or not _ORDER_ID_RE.fullmatch(normalized):
        raise ValueError("order_id must be a numeric WEEX order ID.")
    if int(normalized) > 9223372036854775807:
        raise ValueError("order_id is outside the WEEX Long range.")
    return normalized


def _normalize_client_order_id(value: Any) -> str:
    normalized = str(value or "").strip()
    if not normalized or not _CLIENT_ORDER_ID_RE.fullmatch(normalized):
        raise ValueError("client_order_id must match WEEX order ID format and be 36 characters or fewer.")
    return normalized


def _positive_decimal(value: Any, field: str) -> str:
    normalized = str(value or "").strip()
    try:
        number = Decimal(normalized)
    except (InvalidOperation, ValueError):
        raise ValueError(f"{field} must be a positive number.") from None
    if not number.is_finite() or number <= 0:
        raise ValueError(f"{field} must be a positive number.")
    return normalized


def _prepare_signed_request(*, base_url: str, method: str, path: str, query: dict[str, str], body: dict[str, Any], api_key: str, api_secret: str, api_passphrase: str, timestamp_ms: str) -> dict[str, Any]:
    query_string = urlencode(query)
    body_str = _compact_json(body) if method != "DELETE" else ""
    signature = _sign(api_secret=api_secret, timestamp_ms=timestamp_ms, method=method, path=path, query_string=query_string, body=body_str)
    url = f"{base_url}{path}" + (f"?{query_string}" if query_string else "")
    return {
        "method": method,
        "url": url,
        "headers": {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "locale": "en-US",
            "User-Agent": "weex-mcp/0.1 trade",
            "ACCESS-KEY": api_key,
            "ACCESS-PASSPHRASE": api_passphrase,
            "ACCESS-TIMESTAMP": timestamp_ms,
            "ACCESS-SIGN": signature,
        },
        "data": body_str.encode("utf-8") if body_str else None,
        "query": query,
        "body": body,
    }


def _sign(*, api_secret: str, timestamp_ms: str, method: str, path: str, query_string: str, body: str) -> str:
    message = f"{timestamp_ms}{method}{path}" + (f"?{query_string}" if query_string else "") + body
    digest = hmac.new(api_secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def _default_transport(prepared: dict[str, Any], timeout_seconds: float) -> bytes:
    request = Request(url=prepared["url"], method=prepared["method"], data=prepared["data"], headers=prepared["headers"])
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            return response.read()
    except HTTPError as exc:
        error_body = exc.read()
        payload: Any | None = None
        if error_body:
            try:
                payload = json.loads(error_body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = None
        if payload is not None:
            try:
                raise_for_weex_business_error(payload)
            except WeexRestBusinessError as business_error:
                raise business_error from exc
        raise WeexRestBusinessError(
            code=f"http_{exc.code}",
            message="WEEX Trade API returned a non-success HTTP status.",
            payload=payload,
        ) from exc


def _compact_json(value: dict[str, Any]) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False) if value else ""


def _extract_mapping_payload(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        return dict(payload["data"])
    if isinstance(payload, dict):
        return dict(payload)
    raise WeexRestBusinessError(code="invalid_response", message="WEEX response must be an object.", payload=payload)


def _extract_symbol_leverages(payload: Any, *, symbol: str) -> dict[str, str]:
    data = payload.get("data") if isinstance(payload, dict) and "data" in payload else payload
    items = data if isinstance(data, list) else [data]
    config = next(
        (
            item
            for item in items
            if isinstance(item, dict)
            and str(item.get("symbol") or "").upper() == symbol.upper()
        ),
        None,
    )
    if config is None:
        raise WeexRestBusinessError(
            code="invalid_response",
            message="WEEX symbol configuration did not contain the requested symbol.",
            payload=payload,
        )
    result: dict[str, str] = {}
    for field in ("crossLeverage", "isolatedLongLeverage", "isolatedShortLeverage"):
        raw_value = config.get(field)
        normalized = str(raw_value or "").strip()
        try:
            value = Decimal(normalized)
        except (InvalidOperation, ValueError):
            value = Decimal("NaN")
        if not value.is_finite() or value <= 0:
            raise WeexRestBusinessError(
                code="invalid_response",
                message="WEEX symbol configuration contained invalid leverage values.",
                payload=payload,
            )
        result[field] = normalized
    return result


def _raise_if_trade_rejected(outer: Any, inner: dict[str, Any]) -> None:
    for candidate in (outer, inner):
        if isinstance(candidate, dict) and candidate.get("success") is False:
            code = str(candidate.get("errorCode") or candidate.get("code") or "trade_rejected")
            message = str(candidate.get("errorMessage") or candidate.get("msg") or "WEEX rejected the trade request.")
            raise WeexRestBusinessError(code=code, message=message, payload=outer)


def _has_success_code(payload: Any, *, endpoint: str | None = None) -> bool:
    if not isinstance(payload, dict):
        return False
    code = str(payload.get("code") or "")
    return code in SUCCESS_CODES or (endpoint == FUTURES_MARGIN_MODE_PATH and code == "200")


def _leverage_response_matches_request(*, request_body: dict[str, Any], response_payload: dict[str, Any]) -> bool:
    symbol = request_body.get("symbol")
    margin_type = request_body.get("marginType")
    if symbol in (None, "") or margin_type in (None, ""):
        return False
    if str(response_payload.get("symbol") or "").upper() != str(symbol).upper():
        return False
    if str(response_payload.get("marginType") or "").upper() != str(margin_type).upper():
        return False
    if str(margin_type).upper() == "CROSSED":
        return str(response_payload.get("crossLeverage")) == str(request_body.get("crossLeverage"))
    leverage_fields = ("crossLeverage", "isolatedLongLeverage", "isolatedShortLeverage")
    expected = [(field, request_body.get(field)) for field in leverage_fields if request_body.get(field) not in (None, "")]
    return bool(expected) and all(str(response_payload.get(field)) == str(value) for field, value in expected)


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)
