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
SPOT_BATCH_ORDER_PATH = "/api/v3/order/batch"
FUTURES_BATCH_ORDER_PATH = "/capi/v3/batchOrders"
SPOT_CANCEL_SCOPE_PATH = "/api/v3/openOrders"
FUTURES_CANCEL_SCOPE_PATH = "/capi/v3/allOpenOrders"
FUTURES_ALGO_SCOPE_PATH = "/capi/v3/algoOpenOrders"
FUTURES_ALGO_ORDER_PATH = "/capi/v3/algoOrder"
FUTURES_PLACE_TP_SL_PATH = "/capi/v3/placeTpSlOrder"
FUTURES_MODIFY_TP_SL_PATH = "/capi/v3/modifyTpSlOrder"
FUTURES_CLOSE_POSITIONS_PATH = "/capi/v3/closePositions"
FUTURES_POSITION_MARGIN_PATH = "/capi/v3/account/positionMargin"
FUTURES_AUTO_APPEND_MARGIN_PATH = "/capi/v3/account/modifyAutoAppendMargin"
FUTURES_POSITIONS_PATH = "/capi/v3/account/position/allPosition"
SPOT_USDT_MIN_ORDER_VALUE = Decimal("5")
FUTURES_HTTP_200_SUCCESS_PATHS = {
    FUTURES_MARGIN_MODE_PATH,
    FUTURES_POSITION_MARGIN_PATH,
    FUTURES_AUTO_APPEND_MARGIN_PATH,
}

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
        returned_client_order_id = _optional_str(result_payload.get("clientOrderId"))
        requested_client_order_id = _optional_str(body.get("newClientOrderId"))
        if returned_client_order_id and returned_client_order_id != requested_client_order_id:
            raise WeexRestBusinessError(
                code="invalid_response",
                message="WEEX order response did not match the requested client order ID.",
                payload=payload,
            )
        return {
            "exchange_order_id": order_id,
            "client_order_id": returned_client_order_id or requested_client_order_id,
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
        returned_order_id = _optional_str(result_payload.get("orderId"))
        returned_client_order_id = _optional_str(
            result_payload.get("origClientOrderId") or result_payload.get("clientOrderId")
        )
        requested_order_id = _optional_str(query.get("orderId"))
        requested_client_order_id = _optional_str(query.get("origClientOrderId"))
        if requested_order_id and returned_order_id != requested_order_id:
            raise WeexRestBusinessError(
                code="invalid_response",
                message="WEEX cancel response did not match the requested order ID.",
                payload=payload,
            )
        if not requested_order_id and not returned_order_id and not returned_client_order_id:
            raise WeexRestBusinessError(
                code="invalid_response",
                message="WEEX cancel response did not contain a requested order identity.",
                payload=payload,
            )
        if returned_client_order_id and returned_client_order_id != requested_client_order_id:
            raise WeexRestBusinessError(
                code="invalid_response",
                message="WEEX cancel response did not match the requested client order ID.",
                payload=payload,
            )
        if execution.market == "futures" and requested_client_order_id and not returned_client_order_id:
            raise WeexRestBusinessError(
                code="invalid_response",
                message="WEEX futures cancel response lacked the requested client order ID.",
                payload=payload,
            )
        return {
            "exchange_order_id": returned_order_id,
            "client_order_id": returned_client_order_id or requested_client_order_id,
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

    def preview_mutation(
        self,
        *,
        action: str,
        arguments: dict[str, Any],
        credential: Any,
        credential_argument: dict[str, Any],
        context: TenantContext,
    ) -> dict[str, Any]:
        resolved = self._credential_for_request(credential, credential_argument, context)
        market, trading_mode = _normalize_market_mode(arguments)
        if trading_mode != "live":
            raise ValueError("Extended trade mutations only support live trading_mode.")
        builders = {
            "batch_order": self._preview_batch_order,
            "batch_cancel": self._preview_batch_cancel,
            "cancel_scope": self._preview_cancel_scope,
            "conditional_order": self._preview_conditional_order,
            "conditional_cancel": self._preview_conditional_cancel,
            "tp_sl": self._preview_tp_sl,
            "close_positions": self._preview_close_positions,
            "position_margin": self._preview_position_margin,
            "auto_append_margin": self._preview_auto_append_margin,
        }
        builder = builders.get(action)
        if builder is None:
            raise ValueError("Unsupported trade mutation action.")
        return builder(arguments=arguments, market=market, trading_mode=trading_mode, credential=resolved)

    def confirm_mutation(
        self,
        *,
        execution: TradeExecution,
        credential: Any,
        credential_argument: dict[str, Any],
        context: TenantContext,
    ) -> dict[str, Any]:
        if execution.trading_mode != "live":
            raise ValueError("Extended trade mutations only support live trading_mode.")
        action = execution.action
        endpoint = str(execution.summary.get("endpoint") or "")
        expected = _mutation_transport(action=action, market=execution.market, endpoint=endpoint)
        if execution.summary.get("action") != action:
            raise ValueError("Execution action binding is invalid.")
        method, binding_field = expected
        binding = execution.summary.get(binding_field)
        if not isinstance(binding, dict):
            raise ValueError("Execution request binding is invalid.")
        credential = self._credential_for_request(credential, credential_argument, context)
        position_before: dict[str, Any] | None = None
        margin_before: Decimal | None = None
        if action == "position_margin":
            position_id = _normalize_order_id(binding.get("isolatedPositionId"))
            position_before = _require_isolated_position(self._load_positions(credential), position_id)
            margin_before = _required_position_decimal(position_before, "isolatedMargin")
            _position_margin_target(binding=binding, margin_before=margin_before)
        elif action == "auto_append_margin":
            position_id = _normalize_order_id(binding.get("positionId"))
            position_before = _require_isolated_position(self._load_positions(credential), position_id)
            if not isinstance(binding.get("autoAppendMargin"), bool):
                raise ValueError("Execution auto-append-margin binding is invalid.")
        payload = self._request(
            market=execution.market,
            method=method,
            path=endpoint,
            query={str(key): str(value) for key, value in binding.items()} if binding_field == "request_query" else {},
            body=dict(binding) if binding_field == "request_body" else {},
            credential=credential,
        )
        result = _normalize_mutation_result(
            action=action,
            market=execution.market,
            endpoint=endpoint,
            request_binding=binding,
            payload=payload,
        )
        if action == "position_margin":
            assert margin_before is not None
            position_id = str(binding["isolatedPositionId"])
            position_after = _require_isolated_position(self._load_positions(credential), position_id)
            margin_after = _required_position_decimal(position_after, "isolatedMargin")
            target_margin = _position_margin_target(binding=binding, margin_before=margin_before)
            if margin_after != target_margin:
                raise WeexRestBusinessError(
                    code="invalid_response",
                    message="WEEX position-margin response was not confirmed by position read-back.",
                    payload={"positionId": position_id},
                )
            result["verification"] = {
                "position_id": position_id,
                "isolated_margin_before": str(margin_before),
                "isolated_margin_after": str(margin_after),
            }
        elif action == "auto_append_margin":
            position_id = str(binding["positionId"])
            position_after = _require_isolated_position(self._load_positions(credential), position_id)
            enabled_after = position_after.get("isAutoAppendIsolatedMargin")
            if not isinstance(enabled_after, bool) or enabled_after is not binding["autoAppendMargin"]:
                raise WeexRestBusinessError(
                    code="invalid_response",
                    message="WEEX auto-append-margin response was not confirmed by position read-back.",
                    payload={"positionId": position_id},
                )
            result["verification"] = {
                "position_id": position_id,
                "auto_append_margin_before": position_before.get("isAutoAppendIsolatedMargin") if position_before else None,
                "auto_append_margin_after": enabled_after,
            }
        return result

    def _preview_batch_order(self, *, arguments: dict[str, Any], market: str, trading_mode: str, credential: Any) -> dict[str, Any]:
        raw_orders = arguments.get("orders")
        maximum = 10 if market == "spot" else 5
        if not isinstance(raw_orders, list) or not 1 <= len(raw_orders) <= maximum:
            raise ValueError(f"orders must contain between 1 and {maximum} items.")
        bodies: list[dict[str, Any]] = []
        client_ids: set[str] = set()
        spot_symbol: str | None = None
        for raw_order in raw_orders:
            if not isinstance(raw_order, dict):
                raise ValueError("Each batch order must be an object.")
            order_arguments = {**raw_order, "market": market, "trading_mode": trading_mode}
            body = _build_order_body(order_arguments, market=market, trading_mode=trading_mode)
            client_id = str(body["newClientOrderId"])
            if client_id in client_ids:
                raise ValueError("client_order_id values must be unique within a batch.")
            client_ids.add(client_id)
            symbol = str(body["symbol"])
            if market == "spot":
                if spot_symbol is None:
                    spot_symbol = symbol
                elif symbol != spot_symbol:
                    raise ValueError("All Spot batch orders must use the same symbol.")
            rule = _find_symbol_rule(
                self.market_adapter.get_exchange_info(market=market, symbol=symbol),
                symbol=symbol,
                market=market,
            )
            _validate_order_rules(body, rule, market=market)
            bodies.append(body)
        if market == "spot":
            assert spot_symbol is not None
            body = {"symbol": spot_symbol, "orderList": [{key: value for key, value in item.items() if key != "symbol"} for item in bodies]}
            endpoint = SPOT_BATCH_ORDER_PATH
        else:
            body = {"batchOrders": bodies}
            endpoint = FUTURES_BATCH_ORDER_PATH
        return _preview(
            action="batch_order",
            market=market,
            trading_mode=trading_mode,
            endpoint=endpoint,
            body=body,
            risk_alerts=["batch_order_requires_confirmation", f"batch_size:{len(bodies)}"],
        )

    def _preview_batch_cancel(self, *, arguments: dict[str, Any], market: str, trading_mode: str, credential: Any) -> dict[str, Any]:
        order_ids = arguments.get("order_ids")
        client_ids = arguments.get("client_order_ids")
        if (order_ids is None) == (client_ids is None):
            raise ValueError("Exactly one of order_ids or client_order_ids is required.")
        values = order_ids if order_ids is not None else client_ids
        if not isinstance(values, list) or not 1 <= len(values) <= 10:
            raise ValueError("Batch cancellation accepts between 1 and 10 identifiers.")
        if order_ids is not None:
            normalized = [_normalize_order_id(value) for value in order_ids]
            field = "orderIds" if market == "spot" else "orderIdList"
        else:
            if not isinstance(client_ids, list):
                raise ValueError("client_order_ids must be a list.")
            normalized = [_normalize_client_order_id(value) for value in client_ids]
            field = "origClientOrderIds" if market == "spot" else "origClientOrderIdList"
        if len(set(normalized)) != len(normalized):
            raise ValueError("Batch cancellation identifiers must be unique.")
        return _preview(
            action="batch_cancel",
            market=market,
            trading_mode=trading_mode,
            endpoint=SPOT_BATCH_ORDER_PATH if market == "spot" else FUTURES_BATCH_ORDER_PATH,
            body={field: normalized},
            risk_alerts=["batch_cancel_requires_confirmation", f"batch_size:{len(normalized)}"],
        )

    def _preview_cancel_scope(self, *, arguments: dict[str, Any], market: str, trading_mode: str, credential: Any) -> dict[str, Any]:
        order_kind = str(arguments.get("order_kind") or "").strip().lower()
        scope = str(arguments.get("scope") or "").strip().lower()
        if order_kind not in {"normal", "conditional"} or scope not in {"symbol", "all"}:
            raise ValueError("order_kind and scope are invalid.")
        if market == "spot" and (order_kind != "normal" or scope != "symbol"):
            raise ValueError("Spot scope cancellation only supports normal orders for one symbol.")
        query: dict[str, str] = {}
        if scope == "symbol":
            query["symbol"] = _normalize_symbol(arguments.get("symbol"))
            if arguments.get("acknowledge_all_symbols") not in (None, False):
                raise ValueError("acknowledge_all_symbols is only valid for scope=all.")
        else:
            if market != "futures" or arguments.get("acknowledge_all_symbols") is not True or arguments.get("symbol") not in (None, ""):
                raise ValueError("scope=all requires explicit acknowledgement and no symbol.")
        endpoint = SPOT_CANCEL_SCOPE_PATH if market == "spot" else (
            FUTURES_CANCEL_SCOPE_PATH if order_kind == "normal" else FUTURES_ALGO_SCOPE_PATH
        )
        return _preview(
            action="cancel_scope",
            market=market,
            trading_mode=trading_mode,
            endpoint=endpoint,
            query=query,
            risk_alerts=["scope_cancel_requires_confirmation", f"scope:{scope}", f"order_kind:{order_kind}"],
        )

    def _preview_conditional_order(self, *, arguments: dict[str, Any], market: str, trading_mode: str, credential: Any) -> dict[str, Any]:
        if market != "futures":
            raise ValueError("Conditional orders only support futures.")
        body = _build_conditional_order_body(arguments)
        rule = _find_symbol_rule(
            self.market_adapter.get_exchange_info(market="futures", symbol=body["symbol"]),
            symbol=body["symbol"],
            market="futures",
        )
        _validate_order_rules(
            {"type": "LIMIT" if "price" in body else "MARKET", "quantity": body["quantity"], **({"price": body["price"]} if "price" in body else {})},
            rule,
            market="futures",
        )
        _validate_trigger_price(body["triggerPrice"], "triggerPrice", rule)
        return _preview(
            action="conditional_order",
            market=market,
            trading_mode=trading_mode,
            endpoint=FUTURES_ALGO_ORDER_PATH,
            body=body,
            risk_alerts=["conditional_order_requires_confirmation"],
        )

    def _preview_conditional_cancel(self, *, arguments: dict[str, Any], market: str, trading_mode: str, credential: Any) -> dict[str, Any]:
        if market != "futures":
            raise ValueError("Conditional cancellation only supports futures.")
        return _preview(
            action="conditional_cancel",
            market=market,
            trading_mode=trading_mode,
            endpoint=FUTURES_ALGO_ORDER_PATH,
            query={"orderId": _normalize_order_id(arguments.get("order_id"))},
            risk_alerts=["conditional_cancel_requires_confirmation"],
        )

    def _preview_tp_sl(self, *, arguments: dict[str, Any], market: str, trading_mode: str, credential: Any) -> dict[str, Any]:
        if market != "futures":
            raise ValueError("Standalone TP/SL only supports futures.")
        operation = str(arguments.get("operation") or "").strip().lower()
        if operation == "place":
            body = _build_tp_sl_place_body(arguments)
            rule = _find_symbol_rule(
                self.market_adapter.get_exchange_info(market="futures", symbol=body["symbol"]),
                symbol=body["symbol"],
                market="futures",
            )
            _validate_trigger_price(body["triggerPrice"], "triggerPrice", rule)
            quantity = body.get("quantity")
            if quantity in (None, "0"):
                if arguments.get("acknowledge_full_position") is not True:
                    raise ValueError("Full-position TP/SL requires explicit acknowledgement.")
            else:
                _validate_order_rules({"type": "MARKET", "quantity": quantity}, rule, market="futures")
            endpoint = FUTURES_PLACE_TP_SL_PATH
        elif operation == "modify":
            body = _build_tp_sl_modify_body(arguments)
            endpoint = FUTURES_MODIFY_TP_SL_PATH
        else:
            raise ValueError("operation must be place or modify.")
        return _preview(
            action="tp_sl",
            market=market,
            trading_mode=trading_mode,
            endpoint=endpoint,
            body=body,
            risk_alerts=["tp_sl_mutation_requires_confirmation", f"operation:{operation}"],
        )

    def _preview_close_positions(self, *, arguments: dict[str, Any], market: str, trading_mode: str, credential: Any) -> dict[str, Any]:
        if market != "futures":
            raise ValueError("Position close only supports futures.")
        scope = str(arguments.get("scope") or "").strip().lower()
        body: dict[str, Any] = {}
        if scope == "position":
            body["positionId"] = _normalize_order_id(arguments.get("position_id"))
            if arguments.get("symbol") not in (None, ""):
                body["symbol"] = _normalize_symbol(arguments["symbol"])
        elif scope == "symbol":
            body["symbol"] = _normalize_symbol(arguments.get("symbol"))
            if arguments.get("position_id") not in (None, ""):
                raise ValueError("scope=symbol does not accept position_id.")
        elif scope == "all":
            if arguments.get("acknowledge_all_positions") is not True or any(arguments.get(field) not in (None, "") for field in ("position_id", "symbol")):
                raise ValueError("scope=all requires explicit acknowledgement and no position selector.")
        else:
            raise ValueError("scope must be position, symbol, or all.")
        positions = self._load_positions(credential)
        _validate_position_scope(positions, scope=scope, body=body)
        return _preview(
            action="close_positions",
            market=market,
            trading_mode=trading_mode,
            endpoint=FUTURES_CLOSE_POSITIONS_PATH,
            body=body,
            risk_alerts=["close_positions_requires_confirmation", f"scope:{scope}"],
        )

    def _preview_position_margin(self, *, arguments: dict[str, Any], market: str, trading_mode: str, credential: Any) -> dict[str, Any]:
        if market != "futures":
            raise ValueError("Position margin adjustment only supports futures.")
        position_id = _normalize_order_id(arguments.get("isolated_position_id"))
        position = _require_isolated_position(self._load_positions(credential), position_id)
        adjustment = str(arguments.get("adjustment") or "").strip().lower()
        if adjustment not in {"increase", "decrease"}:
            raise ValueError("adjustment must be increase or decrease.")
        body = {
            "isolatedPositionId": position_id,
            "amount": _positive_decimal(arguments.get("amount"), "amount"),
            "type": 1 if adjustment == "increase" else 2,
        }
        _position_margin_target(
            binding=body,
            margin_before=_required_position_decimal(position, "isolatedMargin"),
        )
        return _preview(
            action="position_margin",
            market=market,
            trading_mode=trading_mode,
            endpoint=FUTURES_POSITION_MARGIN_PATH,
            body=body,
            risk_alerts=["position_margin_requires_confirmation", f"adjustment:{adjustment}"],
        )

    def _preview_auto_append_margin(self, *, arguments: dict[str, Any], market: str, trading_mode: str, credential: Any) -> dict[str, Any]:
        if market != "futures":
            raise ValueError("Auto-append margin only supports futures.")
        position_id = _normalize_order_id(arguments.get("position_id"))
        _require_isolated_position(self._load_positions(credential), position_id)
        enabled = arguments.get("enabled")
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be a boolean.")
        return _preview(
            action="auto_append_margin",
            market=market,
            trading_mode=trading_mode,
            endpoint=FUTURES_AUTO_APPEND_MARGIN_PATH,
            body={"positionId": position_id, "autoAppendMargin": enabled},
            risk_alerts=["auto_append_margin_requires_confirmation", f"enabled:{str(enabled).lower()}"],
        )

    def _load_positions(self, credential: Any) -> list[dict[str, Any]]:
        payload = self._request(
            market="futures",
            method="GET",
            path=FUTURES_POSITIONS_PATH,
            query={},
            body={},
            credential=credential,
        )
        data = payload.get("data") if isinstance(payload, dict) and "data" in payload else payload
        if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
            raise WeexRestBusinessError(code="invalid_response", message="WEEX positions response was invalid.", payload=payload)
        return data

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
            path in FUTURES_HTTP_200_SUCCESS_PATHS
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

    def preview_mutation(self, **_: Any) -> dict[str, Any]:
        raise ValueError("WeexFuturesTradeRestAdapter does not support extended mutations.")

    def confirm_mutation(self, **_: Any) -> dict[str, Any]:
        raise ValueError("WeexFuturesTradeRestAdapter does not support extended mutations.")


def _preview(*, action: str, market: str, trading_mode: str, endpoint: str, body: dict[str, Any] | None = None, query: dict[str, str] | None = None, risk_alerts: list[str]) -> dict[str, Any]:
    summary: dict[str, Any] = {"action": action, "market": market, "trading_mode": trading_mode, "endpoint": endpoint}
    if body is not None:
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
    if query is not None:
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


def _nonnegative_decimal(value: Any, field: str) -> str:
    normalized = str(value if value is not None else "").strip()
    try:
        number = Decimal(normalized)
    except (InvalidOperation, ValueError):
        raise ValueError(f"{field} must be a non-negative number.") from None
    if not number.is_finite() or number < 0:
        raise ValueError(f"{field} must be a non-negative number.")
    return normalized


def _build_conditional_order_body(arguments: dict[str, Any]) -> dict[str, Any]:
    conditional_type = _normalize_enum(
        arguments.get("conditional_type"),
        {"STOP", "TAKE_PROFIT", "STOP_MARKET", "TAKE_PROFIT_MARKET"},
        "conditional_type",
    )
    body: dict[str, Any] = {
        "symbol": _normalize_symbol(arguments.get("symbol")),
        "side": _normalize_enum(arguments.get("side"), {"BUY", "SELL"}, "side"),
        "positionSide": _normalize_enum(arguments.get("position_side"), {"LONG", "SHORT"}, "position_side"),
        "type": conditional_type,
        "quantity": _positive_decimal(arguments.get("quantity"), "quantity"),
        "triggerPrice": _positive_decimal(arguments.get("trigger_price"), "trigger_price"),
        "clientAlgoId": _normalize_client_order_id(arguments.get("client_algo_id")),
    }
    if conditional_type in {"STOP", "TAKE_PROFIT"}:
        body["price"] = _positive_decimal(arguments.get("price"), "price")
    elif arguments.get("price") not in (None, ""):
        raise ValueError("price must be omitted for market conditional orders.")
    for source, target in (
        ("preset_take_profit_price", "presetTakeProfitPrice"),
        ("preset_stop_loss_price", "presetStopLossPrice"),
    ):
        if arguments.get(source) not in (None, ""):
            body[target] = _positive_decimal(arguments[source], source)
    for source, target in (("tp_working_type", "TpWorkingType"), ("sl_working_type", "SlWorkingType")):
        if arguments.get(source) not in (None, ""):
            body[target] = _normalize_enum(arguments[source], {"CONTRACT_PRICE", "MARK_PRICE"}, source)
    return body


def _build_tp_sl_place_body(arguments: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {
        "symbol": _normalize_symbol(arguments.get("symbol")),
        "clientAlgoId": _normalize_client_order_id(arguments.get("client_algo_id")),
        "planType": _normalize_enum(arguments.get("plan_type"), {"TAKE_PROFIT", "STOP_LOSS"}, "plan_type"),
        "triggerPrice": _positive_decimal(arguments.get("trigger_price"), "trigger_price"),
        "positionSide": _normalize_enum(arguments.get("position_side"), {"LONG", "SHORT"}, "position_side"),
    }
    if arguments.get("execute_price") not in (None, ""):
        body["executePrice"] = _nonnegative_decimal(arguments["execute_price"], "execute_price")
    if arguments.get("quantity") not in (None, ""):
        body["quantity"] = _nonnegative_decimal(arguments["quantity"], "quantity")
    if arguments.get("trigger_price_type") not in (None, ""):
        body["triggerPriceType"] = _normalize_enum(
            arguments["trigger_price_type"], {"CONTRACT_PRICE", "MARK_PRICE"}, "trigger_price_type"
        )
    return body


def _build_tp_sl_modify_body(arguments: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {
        "orderId": _normalize_order_id(arguments.get("order_id")),
        "triggerPrice": _positive_decimal(arguments.get("trigger_price"), "trigger_price"),
    }
    if arguments.get("execute_price") not in (None, ""):
        body["executePrice"] = _nonnegative_decimal(arguments["execute_price"], "execute_price")
    if arguments.get("trigger_price_type") not in (None, ""):
        body["triggerPriceType"] = _normalize_enum(
            arguments["trigger_price_type"], {"CONTRACT_PRICE", "MARK_PRICE"}, "trigger_price_type"
        )
    forbidden = ("symbol", "client_algo_id", "plan_type", "quantity", "position_side", "acknowledge_full_position")
    if any(arguments.get(field) not in (None, "") for field in forbidden):
        raise ValueError("TP/SL modify contains place-only fields.")
    return body


def _position_id(item: dict[str, Any]) -> str | None:
    return _optional_str(item.get("positionId") or item.get("isolatedPositionId") or item.get("id"))


def _position_symbol(item: dict[str, Any]) -> str | None:
    value = item.get("symbol")
    if value in (None, ""):
        return None
    try:
        return _normalize_symbol(value)
    except ValueError:
        return None


def _position_is_open(item: dict[str, Any]) -> bool:
    for field in ("positionAmt", "quantity", "size", "availableQty", "holdVol"):
        if item.get(field) in (None, ""):
            continue
        try:
            value = Decimal(str(item[field]))
        except InvalidOperation:
            return False
        return value.is_finite() and value != 0
    return False


def _validate_position_scope(positions: list[dict[str, Any]], *, scope: str, body: dict[str, Any]) -> None:
    open_positions = [item for item in positions if _position_is_open(item)]
    if scope == "position":
        target_id = str(body["positionId"])
        matches = [item for item in open_positions if _position_id(item) == target_id]
        if not matches:
            raise ValueError("position_id does not identify an open position.")
        if "symbol" in body and any(_position_symbol(item) != body["symbol"] for item in matches):
            raise ValueError("position_id does not belong to the supplied symbol.")
    elif scope == "symbol":
        if not any(_position_symbol(item) == body["symbol"] for item in open_positions):
            raise ValueError("No open position matches the supplied symbol.")
    elif not open_positions:
        raise ValueError("The account has no open positions to close.")


def _require_isolated_position(positions: list[dict[str, Any]], position_id: str) -> dict[str, Any]:
    for item in positions:
        if _position_id(item) != position_id:
            continue
        if not _position_is_open(item):
            raise ValueError("position_id does not identify an open position.")
        margin_type = str(item.get("marginType") or item.get("marginMode") or "").strip().upper()
        if margin_type != "ISOLATED":
            raise ValueError("position_id is not an isolated position.")
        return item
    raise ValueError("position_id does not identify an existing position.")


def _required_position_decimal(position: dict[str, Any], field: str) -> Decimal:
    raw = position.get(field)
    if raw in (None, ""):
        raise WeexRestBusinessError(
            code="invalid_response",
            message=f"WEEX position response lacked {field}.",
            payload=position,
        )
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        raise WeexRestBusinessError(
            code="invalid_response",
            message=f"WEEX position response contained invalid {field}.",
            payload=position,
        ) from None
    if not value.is_finite() or value < 0:
        raise WeexRestBusinessError(
            code="invalid_response",
            message=f"WEEX position response contained invalid {field}.",
            payload=position,
        )
    return value


def _position_margin_target(*, binding: dict[str, Any], margin_before: Decimal) -> Decimal:
    try:
        amount = Decimal(str(binding.get("amount") or ""))
    except (InvalidOperation, ValueError):
        raise ValueError("Execution position-margin amount is invalid.") from None
    if not amount.is_finite() or amount <= 0:
        raise ValueError("Execution position-margin amount is invalid.")
    adjustment_type = binding.get("type")
    if adjustment_type not in {1, 2} or isinstance(adjustment_type, bool):
        raise ValueError("Execution position-margin type is invalid.")
    target = margin_before + amount if adjustment_type == 1 else margin_before - amount
    if target < 0:
        raise ValueError("amount exceeds the position's current isolated margin.")
    return target


def _mutation_transport(*, action: str, market: str, endpoint: str) -> tuple[str, str]:
    market = str(market).strip().lower()
    body_bindings: dict[tuple[str, str], tuple[str, str]] = {
        ("batch_order", "spot"): ("POST", SPOT_BATCH_ORDER_PATH),
        ("batch_order", "futures"): ("POST", FUTURES_BATCH_ORDER_PATH),
        ("batch_cancel", "spot"): ("DELETE", SPOT_BATCH_ORDER_PATH),
        ("batch_cancel", "futures"): ("DELETE", FUTURES_BATCH_ORDER_PATH),
        ("conditional_order", "futures"): ("POST", FUTURES_ALGO_ORDER_PATH),
        ("tp_sl", "futures"): ("POST", endpoint),
        ("close_positions", "futures"): ("POST", FUTURES_CLOSE_POSITIONS_PATH),
        ("position_margin", "futures"): ("POST", FUTURES_POSITION_MARGIN_PATH),
        ("auto_append_margin", "futures"): ("POST", FUTURES_AUTO_APPEND_MARGIN_PATH),
    }
    query_bindings: dict[tuple[str, str], tuple[str, set[str]]] = {
        ("cancel_scope", "spot"): ("DELETE", {SPOT_CANCEL_SCOPE_PATH}),
        ("cancel_scope", "futures"): ("DELETE", {FUTURES_CANCEL_SCOPE_PATH, FUTURES_ALGO_SCOPE_PATH}),
        ("conditional_cancel", "futures"): ("DELETE", {FUTURES_ALGO_ORDER_PATH}),
    }
    key = (action, market)
    if key in body_bindings:
        method, expected_endpoint = body_bindings[key]
        allowed = {FUTURES_PLACE_TP_SL_PATH, FUTURES_MODIFY_TP_SL_PATH} if action == "tp_sl" else {expected_endpoint}
        if endpoint not in allowed:
            raise ValueError("Execution endpoint is not allowed for this trade action.")
        return method, "request_body"
    if key in query_bindings:
        method, allowed = query_bindings[key]
        if endpoint not in allowed:
            raise ValueError("Execution endpoint is not allowed for this trade action.")
        return method, "request_query"
    raise ValueError("Unsupported trade mutation action or market.")


def _payload_data(payload: Any) -> Any:
    return payload.get("data") if isinstance(payload, dict) and "data" in payload else payload


def _extract_result_items(payload: Any, *, container: str | None = None) -> list[dict[str, Any]]:
    data = _payload_data(payload)
    if container is not None and isinstance(data, dict):
        data = data.get(container)
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise WeexRestBusinessError(code="invalid_response", message="WEEX mutation response was not a valid item list.", payload=payload)
    return data


def _normalized_item_result(
    item: dict[str, Any],
    *,
    success: bool,
    order_id_field: str = "orderId",
    client_id_field: str = "clientOrderId",
) -> dict[str, Any]:
    if success:
        order_id = _optional_str(item.get(order_id_field))
        if not order_id:
            raise WeexRestBusinessError(code="invalid_response", message="WEEX mutation success item lacked an order ID.", payload=item)
        result: dict[str, Any] = {"ok": True, "order_id": order_id}
        client_id = _optional_str(item.get(client_id_field) or item.get("origClientOrderId"))
        if client_id:
            result["client_order_id"] = client_id
        position_id = _optional_str(item.get("positionId"))
        if position_id:
            result["position_id"] = position_id
        return result
    error_code = _optional_str(item.get("errorCode") or item.get("code"))
    error_message = _optional_str(item.get("errorMessage") or item.get("errorMsg") or item.get("msg"))
    if not error_code and not error_message:
        raise WeexRestBusinessError(code="invalid_response", message="WEEX mutation failure item lacked error evidence.", payload=item)
    return {
        "ok": False,
        "error": {
            "code": error_code or "upstream_rejected",
            "message": error_message or "WEEX rejected the mutation item.",
        },
    }


def _aggregate_items(*, action: str, endpoint: str, items: list[dict[str, Any]], raw: Any) -> dict[str, Any]:
    succeeded = sum(item.get("ok") is True for item in items)
    failed = len(items) - succeeded
    return {
        "action": action,
        "endpoint": endpoint,
        "all_succeeded": failed == 0,
        "succeeded_count": succeeded,
        "failed_count": failed,
        "items": items,
        "status": "accepted" if failed == 0 else "partially_accepted",
        "success": True,
        "raw": raw,
    }


def _normalize_mutation_result(
    *,
    action: str,
    market: str,
    endpoint: str,
    request_binding: dict[str, Any],
    payload: Any,
) -> dict[str, Any]:
    if action in {"batch_order", "batch_cancel"}:
        container = "orderList" if market == "spot" else None
        raw_items = _extract_result_items(payload, container=container)
        identity_kind, requested_identifiers = _batch_request_identifiers(
            action=action,
            market=market,
            request_binding=request_binding,
        )
        if len(raw_items) != len(requested_identifiers) or not raw_items:
            raise WeexRestBusinessError(code="invalid_response", message="WEEX batch response count did not match the request.", payload=payload)
        normalized: list[dict[str, Any]] = []
        for item, requested_identifier in zip(raw_items, requested_identifiers, strict=True):
            if action == "batch_order":
                success = item.get("success") is True if market == "futures" else (
                    item.get("orderId") not in (None, "") and not item.get("errorCode") and not item.get("errorMsg")
                )
            else:
                success = item.get("success") is True if market == "futures" else (
                    str(item.get("status") or "").upper() in {"CANCELED", "CANCELLED"} and item.get("orderId") not in (None, "")
                )
            if market == "futures" and item.get("success") not in {True, False}:
                raise WeexRestBusinessError(code="invalid_response", message="WEEX batch item lacked a success boolean.", payload=payload)
            returned_identifier = _batch_response_identifier(item, identity_kind=identity_kind)
            if returned_identifier and returned_identifier != requested_identifier:
                raise WeexRestBusinessError(
                    code="invalid_response",
                    message="WEEX batch response item did not match the requested identifier.",
                    payload=payload,
                )
            if success and identity_kind != "spot_client_order_id" and not returned_identifier:
                raise WeexRestBusinessError(
                    code="invalid_response",
                    message="WEEX batch success item lacked its requested identifier.",
                    payload=payload,
                )
            item_result = _normalized_item_result(item, success=success)
            item_result[
                "requested_order_id" if identity_kind == "order_id" else "client_order_id"
            ] = requested_identifier
            normalized.append(item_result)
        return _aggregate_items(action=action, endpoint=endpoint, items=normalized, raw=payload)

    if action == "cancel_scope":
        raw_items = _extract_result_items(payload)
        normalized = []
        for item in raw_items:
            success = item.get("success") is True if market == "futures" else (
                str(item.get("status") or "").upper() in {"CANCELED", "CANCELLED"} and item.get("orderId") not in (None, "")
            )
            if market == "futures" and item.get("success") not in {True, False}:
                raise WeexRestBusinessError(code="invalid_response", message="WEEX scope-cancel item lacked a success boolean.", payload=payload)
            normalized.append(_normalized_item_result(item, success=success))
        return _aggregate_items(action=action, endpoint=endpoint, items=normalized, raw=payload)

    if action in {"conditional_order", "conditional_cancel"}:
        item = _extract_mapping_payload(payload)
        _raise_if_trade_rejected(payload, item)
        if item.get("success") is not True:
            raise WeexRestBusinessError(code="invalid_response", message="WEEX conditional mutation lacked success evidence.", payload=payload)
        if action == "conditional_cancel" and str(item.get("orderId") or "") != str(request_binding.get("orderId") or ""):
            raise WeexRestBusinessError(
                code="invalid_response",
                message="WEEX conditional cancellation response did not match the requested order.",
                payload=payload,
            )
        item_result = _normalized_item_result(item, success=True)
        return {"action": action, "endpoint": endpoint, **item_result, "status": "accepted", "success": True, "raw": payload}

    if action == "tp_sl":
        if endpoint == FUTURES_PLACE_TP_SL_PATH:
            raw_items = _extract_result_items(payload)
            if len(raw_items) != 1:
                raise WeexRestBusinessError(
                    code="invalid_response",
                    message="WEEX TP/SL placement must return exactly one result.",
                    payload=payload,
                )
            normalized = []
            for item in raw_items:
                if item.get("success") not in {True, False}:
                    raise WeexRestBusinessError(code="invalid_response", message="WEEX TP/SL item lacked a success boolean.", payload=payload)
                normalized.append(_normalized_item_result(item, success=item["success"] is True))
            return _aggregate_items(action=action, endpoint=endpoint, items=normalized, raw=payload)
        item = _extract_mapping_payload(payload)
        if item.get("success") is not True:
            raise WeexRestBusinessError(code="invalid_response", message="WEEX TP/SL modification lacked success evidence.", payload=payload)
        return {"action": action, "endpoint": endpoint, "status": "accepted", "success": True, "raw": payload}

    if action == "close_positions":
        raw_items = _extract_result_items(payload)
        if not raw_items:
            raise WeexRestBusinessError(code="invalid_response", message="WEEX closePositions returned no result.", payload=payload)
        requested_position_id = _optional_str(request_binding.get("positionId"))
        if requested_position_id is not None and (
            len(raw_items) != 1 or _optional_str(raw_items[0].get("positionId")) != requested_position_id
        ):
            raise WeexRestBusinessError(
                code="invalid_response",
                message="WEEX closePositions response did not match the requested position.",
                payload=payload,
            )
        normalized = []
        for item in raw_items:
            success = item.get("success") is True
            if item.get("success") not in {True, False}:
                raise WeexRestBusinessError(code="invalid_response", message="WEEX closePositions item lacked success evidence.", payload=payload)
            normalized.append(_normalized_item_result(item, success=success, order_id_field="successOrderId"))
        return _aggregate_items(action=action, endpoint=endpoint, items=normalized, raw=payload)

    if action in {"position_margin", "auto_append_margin"}:
        item = _extract_mapping_payload(payload)
        if not _has_success_code(payload, endpoint=endpoint) and item.get("success") is not True:
            raise WeexRestBusinessError(code="invalid_response", message="WEEX account mutation lacked a known success code.", payload=payload)
        return {"action": action, "endpoint": endpoint, "status": "accepted", "success": True, "raw": payload}

    raise ValueError("Unsupported mutation response action.")


def _batch_request_identifiers(
    *,
    action: str,
    market: str,
    request_binding: dict[str, Any],
) -> tuple[str, list[str]]:
    if action == "batch_order":
        field = "orderList" if market == "spot" else "batchOrders"
        request_items = request_binding.get(field)
        if not isinstance(request_items, list) or not all(isinstance(item, dict) for item in request_items):
            raise WeexRestBusinessError(
                code="invalid_response",
                message="WEEX batch-order request binding was invalid.",
                payload=request_binding,
            )
        identifiers = [_optional_str(item.get("newClientOrderId")) for item in request_items]
        if any(identifier in (None, "") for identifier in identifiers):
            raise WeexRestBusinessError(
                code="invalid_response",
                message="WEEX batch-order request lacked client order identifiers.",
                payload=request_binding,
            )
        return "client_order_id", [str(identifier) for identifier in identifiers]

    order_field = "orderIds" if market == "spot" else "orderIdList"
    client_field = "origClientOrderIds" if market == "spot" else "origClientOrderIdList"
    if isinstance(request_binding.get(order_field), list):
        return "order_id", [str(value) for value in request_binding[order_field]]
    if isinstance(request_binding.get(client_field), list):
        identity_kind = "spot_client_order_id" if market == "spot" else "client_order_id"
        return identity_kind, [str(value) for value in request_binding[client_field]]
    raise WeexRestBusinessError(
        code="invalid_response",
        message="WEEX batch-cancel request binding lacked an identifier list.",
        payload=request_binding,
    )


def _batch_response_identifier(item: dict[str, Any], *, identity_kind: str) -> str | None:
    if identity_kind == "order_id":
        return _optional_str(item.get("orderId"))
    if identity_kind == "spot_client_order_id":
        return _optional_str(item.get("origClientOrderId") or item.get("clientOrderId"))
    return _optional_str(item.get("clientOrderId") or item.get("origClientOrderId"))


def _prepare_signed_request(*, base_url: str, method: str, path: str, query: dict[str, str], body: dict[str, Any], api_key: str, api_secret: str, api_passphrase: str, timestamp_ms: str) -> dict[str, Any]:
    query_string = urlencode(query)
    body_str = _compact_json(body)
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
    return code in SUCCESS_CODES or (endpoint in FUTURES_HTTP_200_SUCCESS_PATHS and code == "200")


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
