from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from collections.abc import Callable
from typing import Any, Protocol
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ..secret_provider import SecretProvider
from ..tenant_context import TenantContext
from .rest_errors import WeexRestBusinessError, raise_for_weex_business_error

SPOT_BASE_URL = "https://api-spot.weex.com"
FUTURES_BASE_URL = "https://api-contract.weex.com"
SPOT_BALANCE_PATH = "/api/v3/account"
SPOT_OPEN_ORDERS_PATH = "/api/v3/openOrders"
SPOT_ALL_ORDERS_PATH = "/api/v3/allOrders"
SPOT_ORDER_PATH = "/api/v3/order"
SPOT_MY_TRADES_PATH = "/api/v3/myTrades"
SPOT_BILLS_PATH = "/api/v3/account/bills"
SPOT_FUNDING_BILLS_PATH = "/api/v3/account/fundingBills"
SPOT_TAX_BILLS_PATH = "/api/v3/tax/income"
SPOT_TRANSFER_RECORDS_PATH = "/api/v3/account/transferRecords"
FUTURES_BALANCE_PATH = "/capi/v3/account/balance"
FUTURES_ACCOUNT_CONFIG_PATH = "/capi/v3/account/accountConfig"
FUTURES_COMMISSION_RATE_PATH = "/capi/v3/account/commissionRate"
FUTURES_SYMBOL_CONFIG_PATH = "/capi/v3/account/symbolConfig"
FUTURES_POSITIONS_PATH = "/capi/v3/account/position/allPosition"
FUTURES_OPEN_ORDERS_PATH = "/capi/v3/openOrders"
FUTURES_ORDER_HISTORY_PATH = "/capi/v3/order/history"
FUTURES_ORDER_PATH = "/capi/v3/order"
FUTURES_USER_TRADES_PATH = "/capi/v3/userTrades"
FUTURES_BILLS_PATH = "/capi/v3/account/income"
FUTURES_SIM_BALANCE_PATH = "/capi/v3/sim/balance"
FUTURES_SIM_POSITIONS_PATH = "/capi/v3/sim/position/allPosition"
FUTURES_SIM_ORDER_HISTORY_PATH = "/capi/v3/sim/order/history"
FUTURES_OPEN_ALGO_ORDERS_PATH = "/capi/v3/openAlgoOrders"
FUTURES_ALL_ALGO_ORDERS_PATH = "/capi/v3/allAlgoOrders"

_DAY_MS = 24 * 60 * 60 * 1000
_FUTURES_ORDER_HISTORY_MAX_RANGE_MS = 90 * _DAY_MS
_FUTURES_INCOME_MAX_RANGE_MS = 100 * _DAY_MS
_FUTURES_TRADE_MAX_RANGE_MS = 7 * _DAY_MS
_FUTURES_TRADE_MAX_AGE_MS = 365 * _DAY_MS
_SPOT_TAX_MONTH_PATTERN = re.compile(r"^[0-9]{4}-(?:0[1-9]|1[0-2])$")

PreparedTransport = Callable[[dict[str, Any], float], bytes]


class AccountAdapter(Protocol):
    def get_balance(
        self,
        *,
        market: str,
        trading_mode: str,
        credential_argument: dict[str, Any],
        context: TenantContext,
    ) -> dict[str, Any]:
        ...

    def get_positions(
        self,
        *,
        market: str,
        trading_mode: str,
        credential_argument: dict[str, Any],
        context: TenantContext,
        symbol: str | None = None,
    ) -> dict[str, Any]:
        ...

    def get_orders(
        self,
        *,
        market: str,
        trading_mode: str,
        credential_argument: dict[str, Any],
        context: TenantContext,
        status: str,
        **filters: Any,
    ) -> dict[str, Any]:
        ...

    def get_trades(
        self,
        *,
        market: str,
        trading_mode: str,
        credential_argument: dict[str, Any],
        context: TenantContext,
        **filters: Any,
    ) -> dict[str, Any]:
        ...

    def get_bills(
        self,
        *,
        market: str,
        trading_mode: str,
        credential_argument: dict[str, Any],
        context: TenantContext,
        **filters: Any,
    ) -> dict[str, Any]:
        ...

    def get_config(self, *, market: str, trading_mode: str, credential_argument: dict[str, Any], context: TenantContext) -> dict[str, Any]:
        ...

    def get_commission_rate(self, *, market: str, trading_mode: str, symbol: str, credential_argument: dict[str, Any], context: TenantContext) -> dict[str, Any]:
        ...

    def get_symbol_config(self, *, market: str, trading_mode: str, symbol: str | None, credential_argument: dict[str, Any], context: TenantContext) -> dict[str, Any]:
        ...

    def get_conditional_orders(self, *, market: str, trading_mode: str, status: str, credential_argument: dict[str, Any], context: TenantContext, **filters: Any) -> dict[str, Any]:
        ...

    def get_transfer_records(self, *, market: str, trading_mode: str, credential_argument: dict[str, Any], context: TenantContext, **filters: Any) -> dict[str, Any]:
        ...


class WeexAccountRestAdapter:
    """WEEX USER_DATA read-only account adapter.

    It constructs signed private REST requests but only for read-only balance
    endpoints. It never stores resolved secrets.
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
    ):
        self.secret_provider = secret_provider
        self.spot_base_url = spot_base_url.rstrip("/")
        self.futures_base_url = futures_base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.transport = transport or _default_transport
        self.time_func = time_func or time.time

    def get_balance(
        self,
        *,
        market: str,
        trading_mode: str,
        credential_argument: dict[str, Any],
        context: TenantContext,
    ) -> dict[str, Any]:
        normalized_market = _normalize_market(market)
        normalized_mode = _normalize_trading_mode(trading_mode)
        if normalized_market == "spot" and normalized_mode != "live":
            raise ValueError("Spot account balance only supports live trading_mode.")

        credential = self.secret_provider.resolve(
            credential_argument=credential_argument,
            context=context,
            required_permission="USER_DATA",
        )
        path = _balance_path(market=normalized_market, trading_mode=normalized_mode)
        payload = self._signed_request(
            market=normalized_market,
            method="GET",
            path=path,
            query={},
            body={},
            credential=credential,
        )
        return _normalize_balance_response(market=normalized_market, trading_mode=normalized_mode, payload=payload)

    def get_positions(
        self,
        *,
        market: str,
        trading_mode: str,
        credential_argument: dict[str, Any],
        context: TenantContext,
        symbol: str | None = None,
    ) -> dict[str, Any]:
        normalized_market = _normalize_market(market)
        normalized_mode = _normalize_trading_mode(trading_mode)
        if normalized_market == "spot":
            raise ValueError("Spot account does not expose positions.")
        credential = self.secret_provider.resolve(
            credential_argument=credential_argument,
            context=context,
            required_permission="USER_DATA",
        )
        path = FUTURES_SIM_POSITIONS_PATH if normalized_mode == "demo" else FUTURES_POSITIONS_PATH
        normalized_symbol = _normalize_account_symbol(symbol, trading_mode=normalized_mode) if symbol else None
        payload = self._signed_request(market=normalized_market, method="GET", path=path, query={}, body={}, credential=credential)
        result = _normalize_private_resource(
            market=normalized_market,
            trading_mode=normalized_mode,
            resource="positions",
            payload=payload,
        )
        if normalized_symbol and isinstance(result["positions"], list):
            result["positions"] = [
                item
                for item in result["positions"]
                if isinstance(item, dict) and str(item.get("symbol") or "").upper() == normalized_symbol
            ]
        return result

    def get_orders(
        self,
        *,
        market: str,
        trading_mode: str,
        credential_argument: dict[str, Any],
        context: TenantContext,
        status: str,
        **filters: Any,
    ) -> dict[str, Any]:
        normalized_market = _normalize_market(market)
        normalized_mode = _normalize_trading_mode(trading_mode)
        _reject_spot_demo(normalized_market, normalized_mode)
        normalized_status = str(status).strip().lower()
        if normalized_status not in {"open", "history", "single"}:
            raise ValueError("status must be one of open, history, single.")
        _validate_order_filters(market=normalized_market, status=normalized_status, filters=filters)
        _validate_order_numeric_filters(market=normalized_market, status=normalized_status, filters=filters)
        credential = self.secret_provider.resolve(
            credential_argument=credential_argument,
            context=context,
            required_permission="USER_DATA",
        )
        path = _orders_path(market=normalized_market, trading_mode=normalized_mode, status=normalized_status)
        query = _build_orders_query(
            market=normalized_market,
            trading_mode=normalized_mode,
            status=normalized_status,
            filters=dict(filters),
        )
        payload = self._signed_request(market=normalized_market, method="GET", path=path, query=query, body={}, credential=credential)
        result = _normalize_private_resource(
            market=normalized_market,
            trading_mode=normalized_mode,
            resource="orders",
            payload=payload,
            extra={"status": normalized_status},
        )
        if (
            normalized_market == "futures"
            and normalized_mode == "demo"
            and normalized_status == "history"
            and filters.get("symbol")
            and isinstance(result["orders"], list)
        ):
            requested_symbol = _normalize_demo_symbol_for_display(str(filters["symbol"]))
            result["orders"] = [
                item
                for item in result["orders"]
                if isinstance(item, dict) and str(item.get("symbol") or "").upper() == requested_symbol
            ]
        return result

    def get_trades(
        self,
        *,
        market: str,
        trading_mode: str,
        credential_argument: dict[str, Any],
        context: TenantContext,
        **filters: Any,
    ) -> dict[str, Any]:
        normalized_market = _normalize_market(market)
        normalized_mode = _normalize_trading_mode(trading_mode)
        _reject_spot_demo(normalized_market, normalized_mode)
        if normalized_market == "futures" and normalized_mode == "demo":
            raise ValueError("Demo futures does not expose a dedicated trade details endpoint in the local catalog.")
        if normalized_market == "spot" and not filters.get("symbol"):
            raise ValueError("symbol is required for spot trade history.")
        _validate_trade_filters(market=normalized_market, filters=filters)
        _validate_trade_numeric_filters(
            market=normalized_market,
            filters=filters,
            now_ms=int(self.time_func() * 1000),
        )
        credential = self.secret_provider.resolve(
            credential_argument=credential_argument,
            context=context,
            required_permission="USER_DATA",
        )
        path = SPOT_MY_TRADES_PATH if normalized_market == "spot" else FUTURES_USER_TRADES_PATH
        query = _filter_query(filters)
        payload = self._signed_request(market=normalized_market, method="GET", path=path, query=query, body={}, credential=credential)
        return _normalize_private_resource(market=normalized_market, trading_mode=normalized_mode, resource="trades", payload=payload)

    def get_bills(
        self,
        *,
        market: str,
        trading_mode: str,
        credential_argument: dict[str, Any],
        context: TenantContext,
        **filters: Any,
    ) -> dict[str, Any]:
        normalized_market = _normalize_market(market)
        normalized_mode = _normalize_trading_mode(trading_mode)
        _reject_spot_demo(normalized_market, normalized_mode)
        if normalized_market == "futures" and normalized_mode == "demo":
            raise ValueError("Demo futures does not expose account bills in the local catalog.")
        credential = self.secret_provider.resolve(
            credential_argument=credential_argument,
            context=context,
            required_permission="USER_DATA",
        )
        bill_type = str(filters.pop("bill_type", "") or ("account" if normalized_market == "spot" else "income")).strip().lower()
        _validate_bill_filters(market=normalized_market, bill_type=bill_type, filters=filters)
        _validate_bill_numeric_filters(market=normalized_market, bill_type=bill_type, filters=filters)
        path = _bills_path(market=normalized_market, bill_type=bill_type)
        body = _build_bills_body(market=normalized_market, bill_type=bill_type, filters=filters)
        payload = self._signed_request(market=normalized_market, method="POST", path=path, query={}, body=body, credential=credential)
        return _normalize_private_resource(
            market=normalized_market,
            trading_mode=normalized_mode,
            resource="bills",
            payload=payload,
            extra={"bill_type": bill_type},
        )

    def get_config(
        self,
        *,
        market: str,
        trading_mode: str,
        credential_argument: dict[str, Any],
        context: TenantContext,
    ) -> dict[str, Any]:
        normalized_market, normalized_mode = _require_live_account_mode(market, trading_mode)
        credential = self.secret_provider.resolve(
            credential_argument=credential_argument,
            context=context,
            required_permission="USER_DATA",
        )
        path = SPOT_BALANCE_PATH if normalized_market == "spot" else FUTURES_ACCOUNT_CONFIG_PATH
        payload = self._signed_request(
            market=normalized_market, method="GET", path=path, query={}, body={}, credential=credential
        )
        config = _extract_object_payload(payload, resource="account config")
        if not all(
            isinstance(config.get(field), bool) for field in ("canTrade", "canDeposit", "canWithdraw")
        ):
            raise WeexRestBusinessError(
                code="invalid_response",
                message="WEEX account config was missing boolean capability fields.",
                payload=payload,
            )
        return {
            "market": normalized_market,
            "trading_mode": normalized_mode,
            "config": config,
            "source": "weex_private_rest",
            "raw": payload,
        }

    def get_commission_rate(
        self,
        *,
        market: str,
        trading_mode: str,
        symbol: str,
        credential_argument: dict[str, Any],
        context: TenantContext,
    ) -> dict[str, Any]:
        normalized_market, normalized_mode = _require_live_futures_mode(market, trading_mode)
        normalized_symbol = _normalize_symbol(symbol)
        assert normalized_symbol is not None
        credential = self.secret_provider.resolve(
            credential_argument=credential_argument, context=context, required_permission="USER_DATA"
        )
        payload = self._signed_request(
            market=normalized_market,
            method="GET",
            path=FUTURES_COMMISSION_RATE_PATH,
            query={"symbol": normalized_symbol},
            body={},
            credential=credential,
        )
        rate = _extract_object_payload(payload, resource="commission rate")
        if _normalize_upstream_symbol(rate.get("symbol"), payload=payload) != normalized_symbol or any(
            rate.get(field) in (None, "") for field in ("makerCommissionRate", "takerCommissionRate")
        ):
            raise WeexRestBusinessError(code="invalid_response", message="WEEX commission-rate response was invalid.", payload=payload)
        return {
            "market": normalized_market,
            "trading_mode": normalized_mode,
            "symbol": normalized_symbol,
            "commission_rate": rate,
            "source": "weex_private_rest",
            "raw": payload,
        }

    def get_symbol_config(
        self,
        *,
        market: str,
        trading_mode: str,
        credential_argument: dict[str, Any],
        context: TenantContext,
        symbol: str | None = None,
    ) -> dict[str, Any]:
        normalized_market, normalized_mode = _require_live_futures_mode(market, trading_mode)
        normalized_symbol = _normalize_symbol(symbol) if symbol else None
        credential = self.secret_provider.resolve(
            credential_argument=credential_argument, context=context, required_permission="USER_DATA"
        )
        query: dict[str, Any] = {}
        _put_if_present(query, "symbol", normalized_symbol)
        payload = self._signed_request(
            market=normalized_market,
            method="GET",
            path=FUTURES_SYMBOL_CONFIG_PATH,
            query=query,
            body={},
            credential=credential,
        )
        configs = _extract_object_list_payload(payload, resource="symbol configs")
        if not all(item.get("symbol") not in (None, "") and item.get("marginType") not in (None, "") for item in configs):
            raise WeexRestBusinessError(code="invalid_response", message="WEEX symbol-config response was invalid.", payload=payload)
        if normalized_symbol is not None and (
            not configs or any(_normalize_upstream_symbol(item["symbol"], payload=payload) != normalized_symbol for item in configs)
        ):
            raise WeexRestBusinessError(code="invalid_response", message="WEEX symbol-config response did not match symbol.", payload=payload)
        return {
            "market": normalized_market,
            "trading_mode": normalized_mode,
            "symbol": normalized_symbol,
            "symbol_configs": configs,
            "source": "weex_private_rest",
            "raw": payload,
        }

    def get_conditional_orders(
        self,
        *,
        market: str,
        trading_mode: str,
        status: str,
        credential_argument: dict[str, Any],
        context: TenantContext,
        **filters: Any,
    ) -> dict[str, Any]:
        normalized_market, normalized_mode = _require_live_futures_mode(market, trading_mode)
        normalized_status = str(status).strip().lower()
        if normalized_status not in {"open", "history"}:
            raise ValueError("status must be open or history.")
        _reject_unsupported_filters(
            filters=filters,
            supported={"symbol", "start_time", "end_time", "page", "limit"} if normalized_status == "open" else {"symbol", "start_time", "end_time", "limit"},
            resource="conditional order",
        )
        maximum_span = _FUTURES_ORDER_HISTORY_MAX_RANGE_MS if normalized_status == "history" else None
        _validate_time_range(filters, maximum_span_ms=maximum_span, require_pair=True)
        if filters.get("page") not in (None, ""):
            _validate_catalog_integer(filters["page"], field="page", minimum=1, maximum=2_147_483_647)
        if filters.get("limit") not in (None, ""):
            maximum = 100 if normalized_status == "open" else 1000
            _validate_catalog_integer(filters["limit"], field="limit", minimum=1, maximum=maximum)
        query: dict[str, Any] = {}
        for source, target in (
            ("symbol", "symbol"),
            ("start_time", "startTime"),
            ("end_time", "endTime"),
            ("page", "page"),
            ("limit", "limit"),
        ):
            value = filters.get(source)
            if source == "symbol" and value not in (None, ""):
                value = _normalize_symbol(str(value))
            _put_if_present(query, target, value)
        credential = self.secret_provider.resolve(
            credential_argument=credential_argument, context=context, required_permission="USER_DATA"
        )
        path = FUTURES_OPEN_ALGO_ORDERS_PATH if normalized_status == "open" else FUTURES_ALL_ALGO_ORDERS_PATH
        payload = self._signed_request(
            market=normalized_market, method="GET", path=path, query=query, body={}, credential=credential
        )
        data = payload.get("data") if isinstance(payload, dict) and "data" in payload else payload
        has_more: bool | None = None
        if normalized_status == "history":
            if not isinstance(data, dict) or not isinstance(data.get("orders"), list) or not isinstance(data.get("hasMore"), bool):
                raise WeexRestBusinessError(code="invalid_response", message="WEEX conditional history response was invalid.", payload=payload)
            orders = data["orders"]
            has_more = data["hasMore"]
        else:
            orders = data
        _require_object_items(orders, resource="conditional orders", payload=payload)
        if not all(item.get("algoId") not in (None, "") for item in orders):
            raise WeexRestBusinessError(code="invalid_response", message="WEEX conditional-order item lacked algoId.", payload=payload)
        result = {
            "market": normalized_market,
            "trading_mode": normalized_mode,
            "status": normalized_status,
            "conditional_orders": orders,
            "source": "weex_private_rest",
            "raw": payload,
        }
        if has_more is not None:
            result["has_more"] = has_more
        return result

    def get_transfer_records(
        self,
        *,
        market: str,
        trading_mode: str,
        credential_argument: dict[str, Any],
        context: TenantContext,
        **filters: Any,
    ) -> dict[str, Any]:
        normalized_market, normalized_mode = _require_live_account_mode(market, trading_mode)
        if normalized_market != "spot":
            raise ValueError("Transfer records only support spot.")
        _reject_unsupported_filters(
            filters=filters,
            supported={"coin_id", "from_type", "after", "before", "limit"},
            resource="transfer records",
        )
        _validate_catalog_integer(filters.get("coin_id"), field="coin_id", minimum=0, maximum=2_147_483_647)
        after = _validate_catalog_integer(filters.get("after"), field="after", minimum=0, maximum=9_223_372_036_854_775_807)
        before = _validate_catalog_integer(filters.get("before"), field="before", minimum=0, maximum=9_223_372_036_854_775_807)
        if after is not None and before is not None and after > before:
            raise ValueError("after must be less than or equal to before.")
        _validate_catalog_integer(filters.get("limit"), field="limit", minimum=1, maximum=2_147_483_647)
        query: dict[str, Any] = {}
        for source, target in (("coin_id", "coinId"), ("from_type", "fromType"), ("limit", "limit"), ("after", "after"), ("before", "before")):
            _put_if_present(query, target, filters.get(source))
        credential = self.secret_provider.resolve(
            credential_argument=credential_argument, context=context, required_permission="USER_DATA"
        )
        payload = self._signed_request(
            market=normalized_market,
            method="GET",
            path=SPOT_TRANSFER_RECORDS_PATH,
            query=query,
            body={},
            credential=credential,
        )
        records = _extract_object_list_payload(payload, resource="transfer records")
        return {
            "market": normalized_market,
            "trading_mode": normalized_mode,
            "transfer_records": records,
            "source": "weex_private_rest",
            "raw": payload,
        }

    def _signed_request(
        self,
        *,
        market: str,
        method: str,
        path: str,
        query: dict[str, Any],
        body: dict[str, Any],
        credential: Any,
    ) -> Any:
        base_url = self.spot_base_url if market == "spot" else self.futures_base_url
        query_string = urlencode(query, doseq=True)
        body_string = _compact_json(body) if method != "GET" else ""
        timestamp_ms = str(int(self.time_func() * 1000))
        signature = _sign(
            api_secret=credential.api_secret,
            timestamp_ms=timestamp_ms,
            method=method,
            path=path,
            query_string=query_string,
            body=body_string,
        )
        url = f"{base_url}{path}"
        if query_string:
            url = f"{url}?{query_string}"
        prepared = {
            "method": method,
            "url": url,
            "headers": {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "locale": "en-US",
                "User-Agent": "weex-mcp/0.1 account-readonly",
                "ACCESS-KEY": credential.api_key,
                "ACCESS-PASSPHRASE": credential.api_passphrase,
                "ACCESS-TIMESTAMP": timestamp_ms,
                "ACCESS-SIGN": signature,
            },
            "data": body_string.encode("utf-8") if body_string else None,
            "query": query,
            "body": body,
        }
        raw_bytes = self.transport(prepared, self.timeout_seconds)
        payload = json.loads(raw_bytes.decode("utf-8"))
        raise_for_weex_business_error(payload)
        return payload


def _default_transport(prepared: dict[str, Any], timeout_seconds: float) -> bytes:
    request = Request(
        url=prepared["url"],
        method=prepared["method"],
        data=prepared["data"],
        headers=prepared["headers"],
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        return response.read()


def _sign(*, api_secret: str, timestamp_ms: str, method: str, path: str, query_string: str, body: str) -> str:
    message = f"{timestamp_ms}{method}{path}"
    if query_string:
        message += f"?{query_string}"
    message += body
    digest = hmac.new(api_secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def _compact_json(value: dict[str, Any]) -> str:
    if not value:
        return ""
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _normalize_market(market: str) -> str:
    value = str(market).strip().lower()
    if value == "contract":
        value = "futures"
    if value not in {"spot", "futures"}:
        raise ValueError("Unsupported market.")
    return value


def _normalize_trading_mode(trading_mode: str) -> str:
    value = str(trading_mode).strip().lower()
    if value not in {"live", "demo"}:
        raise ValueError("Unsupported trading_mode.")
    return value


def _require_live_account_mode(market: str, trading_mode: str) -> tuple[str, str]:
    normalized_market = _normalize_market(market)
    normalized_mode = _normalize_trading_mode(trading_mode)
    if normalized_mode != "live":
        raise ValueError("This account query only supports live trading_mode.")
    return normalized_market, normalized_mode


def _require_live_futures_mode(market: str, trading_mode: str) -> tuple[str, str]:
    normalized_market, normalized_mode = _require_live_account_mode(market, trading_mode)
    if normalized_market != "futures":
        raise ValueError("This account query only supports futures.")
    return normalized_market, normalized_mode


def _reject_spot_demo(market: str, trading_mode: str) -> None:
    if market == "spot" and trading_mode == "demo":
        raise ValueError("Spot account queries only support live trading_mode.")


def _balance_path(*, market: str, trading_mode: str) -> str:
    if market == "spot":
        return SPOT_BALANCE_PATH
    if trading_mode == "demo":
        return FUTURES_SIM_BALANCE_PATH
    return FUTURES_BALANCE_PATH


def _orders_path(*, market: str, trading_mode: str, status: str) -> str:
    if market == "spot":
        return {"open": SPOT_OPEN_ORDERS_PATH, "history": SPOT_ALL_ORDERS_PATH, "single": SPOT_ORDER_PATH}[status]
    if trading_mode == "demo":
        if status != "history":
            raise ValueError("Demo futures order query only supports status=history.")
        return FUTURES_SIM_ORDER_HISTORY_PATH
    return {"open": FUTURES_OPEN_ORDERS_PATH, "history": FUTURES_ORDER_HISTORY_PATH, "single": FUTURES_ORDER_PATH}[status]


def _bills_path(*, market: str, bill_type: str) -> str:
    if market == "spot":
        paths = {"account": SPOT_BILLS_PATH, "funding": SPOT_FUNDING_BILLS_PATH, "tax": SPOT_TAX_BILLS_PATH}
        if bill_type not in paths:
            raise ValueError("spot bill_type must be one of account, funding, tax.")
        return paths[bill_type]
    if bill_type != "income":
        raise ValueError("futures bill_type must be income.")
    return FUTURES_BILLS_PATH


def _normalize_balance_response(*, market: str, trading_mode: str, payload: Any) -> dict[str, Any]:
    data = payload.get("data") if isinstance(payload, dict) and "data" in payload else payload
    if isinstance(data, dict) and isinstance(data.get("balances"), list):
        balances = data["balances"]
    elif isinstance(data, list):
        balances = data
    else:
        raise WeexRestBusinessError(
            code="invalid_response",
            message="WEEX account response did not contain a balances list.",
            payload=payload,
        )
    _require_object_items(balances, resource="balances", payload=payload)
    return {
        "market": market,
        "trading_mode": trading_mode,
        "balances": balances,
        "source": "weex_private_rest",
        "raw": payload,
    }


def _extract_object_payload(payload: Any, *, resource: str) -> dict[str, Any]:
    data = payload.get("data") if isinstance(payload, dict) and "data" in payload else payload
    if not isinstance(data, dict):
        raise WeexRestBusinessError(code="invalid_response", message=f"WEEX {resource} response was not an object.", payload=payload)
    return data


def _extract_object_list_payload(payload: Any, *, resource: str) -> list[dict[str, Any]]:
    data = payload.get("data") if isinstance(payload, dict) and "data" in payload else payload
    return _require_object_items(data, resource=resource, payload=payload)


def _normalize_private_resource(
    *,
    market: str,
    trading_mode: str,
    resource: str,
    payload: Any,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = payload.get("data") if isinstance(payload, dict) and "data" in payload else payload
    items: Any
    if isinstance(data, dict) and isinstance(data.get(resource), list):
        items = data[resource]
    elif isinstance(data, list):
        items = data
    elif resource == "orders" and isinstance(data, dict) and (extra or {}).get("status") == "single" and _has_single_order_fields(data):
        items = data
    elif resource == "bills" and (extra or {}).get("bill_type") in {"funding", "income"} and isinstance(data, dict) and isinstance(data.get("items"), list):
        items = data
    else:
        raise WeexRestBusinessError(
            code="invalid_response",
            message=f"WEEX account response did not contain valid {resource} data.",
            payload=payload,
        )
    if isinstance(items, list):
        _require_object_items(items, resource=resource, payload=payload)
    elif resource == "bills" and isinstance(items, dict):
        _require_object_items(items.get("items"), resource="bills", payload=payload)
    items = _normalize_demo_resource_symbols(items, resource=resource, trading_mode=trading_mode)
    result = {
        "market": market,
        "trading_mode": trading_mode,
        resource: items,
        "source": "weex_private_rest",
        "raw": payload,
    }
    if extra:
        result.update(extra)
    return result


def _has_single_order_fields(value: dict[str, Any]) -> bool:
    order_identity = any(value.get(key) not in (None, "") for key in ("orderId", "order_id", "clientOrderId", "client_order_id"))
    order_attributes = any(value.get(key) not in (None, "") for key in ("status", "symbol", "side", "type", "orderType"))
    return order_identity and order_attributes


def _require_object_items(items: Any, *, resource: str, payload: Any) -> list[dict[str, Any]]:
    if isinstance(items, list) and all(isinstance(item, dict) for item in items):
        return items
    raise WeexRestBusinessError(
        code="invalid_response",
        message=f"WEEX account response contained invalid {resource} items.",
        payload=payload,
    )


def _normalize_symbol(symbol: str | None) -> str | None:
    if symbol is None:
        return None
    value = str(symbol).strip().upper().replace("-", "").replace("/", "").replace(" ", "").replace("_", "")
    if not value or not value.isalnum():
        raise ValueError("Unsupported symbol format.")
    return value


def _normalize_upstream_symbol(value: Any, *, payload: Any) -> str:
    try:
        normalized = _normalize_symbol(str(value or ""))
    except ValueError:
        normalized = None
    if normalized is None:
        raise WeexRestBusinessError(
            code="invalid_response",
            message="WEEX account response contained an invalid symbol.",
            payload=payload,
        )
    return normalized


def _normalize_demo_symbol_for_display(symbol: str) -> str:
    normalized = _normalize_symbol(symbol)
    if normalized and normalized.endswith("SUSDT") and len(normalized) > 5:
        return f"{normalized[:-5]}USDT"
    return normalized or ""


def _normalize_demo_symbol_for_request(symbol: str) -> str:
    normalized = _normalize_symbol(symbol)
    assert normalized is not None
    if normalized.endswith("USDT") and not normalized.endswith("SUSDT"):
        return f"{normalized[:-4]}SUSDT"
    return normalized


def _normalize_account_symbol(symbol: str, *, trading_mode: str) -> str:
    normalized = _normalize_symbol(symbol)
    assert normalized is not None
    if trading_mode == "demo":
        return _normalize_demo_symbol_for_display(normalized)
    return normalized


def _normalize_demo_resource_symbols(items: Any, *, resource: str, trading_mode: str) -> Any:
    if trading_mode != "demo" or resource not in {"positions", "orders"} or not isinstance(items, list):
        return items
    normalized_items: list[Any] = []
    for item in items:
        if not isinstance(item, dict) or item.get("symbol") in (None, ""):
            normalized_items.append(item)
            continue
        normalized_item = dict(item)
        try:
            normalized_item["symbol"] = _normalize_demo_symbol_for_display(str(item["symbol"]))
        except ValueError:
            normalized_item["symbol"] = str(item["symbol"])
        normalized_items.append(normalized_item)
    return normalized_items


def _put_if_present(target: dict[str, Any], key: str, value: Any | None) -> None:
    if value is not None and value != "":
        target[key] = value


def _filter_query(filters: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for source_key, target_key in (
        ("symbol", "symbol"),
        ("order_id", "orderId"),
        ("client_order_id", "clientOrderId"),
        ("start_time", "startTime"),
        ("end_time", "endTime"),
        ("limit", "limit"),
    ):
        value = filters.get(source_key)
        if value in (None, ""):
            continue
        if source_key == "symbol":
            value = _normalize_symbol(str(value))
        result[target_key] = value
    return result


def _build_orders_query(*, market: str, trading_mode: str, status: str, filters: dict[str, Any]) -> dict[str, Any]:
    if status == "single":
        query: dict[str, Any] = {}
        _put_if_present(query, "orderId", filters.get("order_id"))
        if market == "spot":
            _put_if_present(query, "origClientOrderId", filters.get("client_order_id"))
        return query
    if status == "open" and market == "spot":
        query = {}
        _put_if_present(query, "symbol", _normalize_symbol(filters.get("symbol")) if filters.get("symbol") else None)
        return query
    if status == "open":
        query = {}
        _put_if_present(query, "symbol", _normalize_symbol(filters.get("symbol")) if filters.get("symbol") else None)
        for source, target in (
            ("order_id", "orderId"),
            ("start_time", "startTime"),
            ("end_time", "endTime"),
            ("limit", "limit"),
            ("page", "page"),
        ):
            _put_if_present(query, target, filters.get(source))
        return query
    if status == "history":
        query = {}
        symbol = filters.get("symbol")
        if symbol and market == "futures" and trading_mode == "demo":
            symbol = _normalize_demo_symbol_for_request(str(symbol))
        elif symbol:
            symbol = _normalize_symbol(str(symbol))
        _put_if_present(query, "symbol", symbol)
        for source, target in (
            ("start_time", "startTime"),
            ("end_time", "endTime"),
            ("limit", "limit"),
            ("page", "page"),
        ):
            _put_if_present(query, target, filters.get(source))
        return query
    return _filter_query(filters)


def _validate_order_filters(*, market: str, status: str, filters: dict[str, Any]) -> None:
    supported: dict[tuple[str, str], set[str]] = {
        ("spot", "open"): {"symbol"},
        ("spot", "history"): {"symbol", "start_time", "end_time", "limit", "page"},
        ("spot", "single"): {"order_id", "client_order_id"},
        ("futures", "open"): {"symbol", "order_id", "start_time", "end_time", "limit", "page"},
        ("futures", "history"): {"symbol", "start_time", "end_time", "limit", "page"},
        ("futures", "single"): {"order_id"},
    }
    _reject_unsupported_filters(filters=filters, supported=supported[(market, status)], resource="order")
    if market == "spot" and status == "history" and not filters.get("symbol"):
        raise ValueError("symbol is required for spot order history.")
    if status != "single":
        return
    if market == "spot" and not (filters.get("order_id") or filters.get("client_order_id")):
        raise ValueError("order_id or client_order_id is required for a spot single-order query.")
    if market == "futures" and not filters.get("order_id"):
        raise ValueError("order_id is required for a futures single-order query.")


def _validate_trade_filters(*, market: str, filters: dict[str, Any]) -> None:
    _reject_unsupported_filters(
        filters=filters,
        supported={"symbol", "order_id", "start_time", "end_time", "limit"},
        resource="trade",
    )


def _validate_bill_filters(*, market: str, bill_type: str, filters: dict[str, Any]) -> None:
    supported_by_type: dict[tuple[str, str], set[str]] = {
        ("spot", "account"): {"coin_id", "biz_type", "start_time", "end_time", "limit"},
        ("spot", "funding"): {"coin_id", "biz_type", "start_time", "end_time", "limit", "page"},
        ("spot", "tax"): {"asset", "biz_type", "month", "limit", "page"},
        ("futures", "income"): {"asset", "symbol", "income_type", "start_time", "end_time", "limit", "next_key_id", "next_key_time"},
    }
    try:
        supported = supported_by_type[(market, bill_type)]
    except KeyError:
        raise ValueError(f"{market} bill_type is not supported.") from None
    _reject_unsupported_filters(filters=filters, supported=supported, resource="bill")


def _validate_order_numeric_filters(*, market: str, status: str, filters: dict[str, Any]) -> None:
    _validate_catalog_integer(filters.get("order_id"), field="order_id", minimum=1, maximum=9_223_372_036_854_775_807)
    _validate_time_range(
        filters,
        maximum_span_ms=_FUTURES_ORDER_HISTORY_MAX_RANGE_MS if market == "futures" and status == "history" else None,
    )
    if filters.get("limit") not in (None, ""):
        maximum = 100 if market == "futures" and status == "open" else 1000
        _validate_catalog_integer(filters["limit"], field="limit", minimum=1, maximum=maximum)
    if filters.get("page") not in (None, ""):
        minimum = 1 if market == "spot" else 0
        _validate_catalog_integer(filters["page"], field="page", minimum=minimum, maximum=2_147_483_647)


def _validate_trade_numeric_filters(*, market: str, filters: dict[str, Any], now_ms: int | None = None) -> None:
    _validate_catalog_integer(filters.get("order_id"), field="order_id", minimum=1, maximum=9_223_372_036_854_775_807)
    _validate_time_range(
        filters,
        maximum_span_ms=_FUTURES_TRADE_MAX_RANGE_MS if market == "futures" else None,
        require_pair=market == "futures",
    )
    if market == "futures" and filters.get("start_time") not in (None, ""):
        start = int(str(filters["start_time"]))
        end = int(str(filters["end_time"]))
        current_ms = int(time.time() * 1000) if now_ms is None else now_ms
        if start < current_ms - _FUTURES_TRADE_MAX_AGE_MS:
            raise ValueError("Futures trade history cannot query more than 365 days in the past.")
        if end > current_ms:
            raise ValueError("Futures trade history cannot query a future end_time.")
    if filters.get("limit") not in (None, ""):
        maximum = 100 if market == "futures" else 2_147_483_647
        _validate_catalog_integer(filters["limit"], field="limit", minimum=1, maximum=maximum)


def _validate_bill_numeric_filters(*, market: str, bill_type: str, filters: dict[str, Any]) -> None:
    _validate_catalog_integer(filters.get("coin_id"), field="coin_id", minimum=0, maximum=2_147_483_647)
    _validate_time_range(
        filters,
        maximum_span_ms=_FUTURES_INCOME_MAX_RANGE_MS if market == "futures" and bill_type == "income" else None,
    )
    if market == "spot" and bill_type == "tax" and filters.get("month") not in (None, ""):
        if not isinstance(filters["month"], str) or _SPOT_TAX_MONTH_PATTERN.fullmatch(filters["month"]) is None:
            raise ValueError("month must use YYYY-MM format with a month from 01 to 12.")
    _validate_catalog_integer(filters.get("next_key_id"), field="next_key_id", minimum=0, maximum=9_223_372_036_854_775_807)
    _validate_catalog_integer(filters.get("next_key_time"), field="next_key_time", minimum=0, maximum=9_223_372_036_854_775_807)
    if filters.get("limit") not in (None, ""):
        maximum = {
            ("spot", "account"): 100,
            ("spot", "funding"): 100,
            ("spot", "tax"): 200,
            ("futures", "income"): 100,
        }[(market, bill_type)]
        _validate_catalog_integer(filters["limit"], field="limit", minimum=1, maximum=maximum)
    if filters.get("page") not in (None, ""):
        _validate_catalog_integer(filters["page"], field="page", minimum=1, maximum=2_147_483_647)


def _validate_time_range(
    filters: dict[str, Any],
    *,
    maximum_span_ms: int | None = None,
    require_pair: bool = False,
) -> None:
    start = _validate_catalog_integer(filters.get("start_time"), field="start_time", minimum=0, maximum=9_223_372_036_854_775_807)
    end = _validate_catalog_integer(filters.get("end_time"), field="end_time", minimum=0, maximum=9_223_372_036_854_775_807)
    if require_pair and (start is None) != (end is None):
        raise ValueError("start_time and end_time must be provided together.")
    if start is not None and end is not None and start > end:
        raise ValueError("start_time must be less than or equal to end_time.")
    if start is not None and end is not None and maximum_span_ms is not None and end - start > maximum_span_ms:
        raise ValueError(f"start_time and end_time must span no more than {maximum_span_ms} milliseconds.")


def _validate_catalog_integer(value: Any, *, field: str, minimum: int, maximum: int) -> int | None:
    if value in (None, ""):
        return None
    normalized = str(value)
    if not normalized.isascii() or not normalized.isdigit() or (len(normalized) > 1 and normalized.startswith("0")):
        raise ValueError(f"{field} must be a canonical integer string.")
    parsed = int(normalized)
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}.")
    return parsed


def _reject_unsupported_filters(*, filters: dict[str, Any], supported: set[str], resource: str) -> None:
    unsupported = sorted(
        key for key, value in filters.items()
        if key not in supported and value not in (None, "")
    )
    if unsupported:
        raise ValueError(f"{resource} query contains filters unsupported by the selected endpoint: {', '.join(unsupported)}.")


def _build_bills_body(*, market: str, bill_type: str, filters: dict[str, Any]) -> dict[str, Any]:
    mapping: tuple[tuple[str, str], ...]
    if market == "spot" and bill_type == "account":
        mapping = (
            ("coin_id", "coinId"),
            ("biz_type", "bizType"),
            ("start_time", "after"),
            ("end_time", "before"),
            ("limit", "limit"),
        )
    elif market == "spot" and bill_type == "funding":
        mapping = (
            ("coin_id", "coinId"),
            ("biz_type", "bizType"),
            ("start_time", "startTime"),
            ("end_time", "endTime"),
            ("page", "pageIndex"),
            ("limit", "pageSize"),
        )
    elif market == "spot":
        mapping = (
            ("asset", "coin"),
            ("biz_type", "bizType"),
            ("month", "month"),
            ("limit", "limit"),
            ("page", "page"),
        )
    else:
        mapping = (
            ("asset", "asset"),
            ("symbol", "symbol"),
            ("income_type", "incomeType"),
            ("start_time", "startTime"),
            ("end_time", "endTime"),
            ("limit", "limit"),
            ("next_key_id", "nextKeyId"),
            ("next_key_time", "nextKeyTime"),
        )

    body: dict[str, Any] = {}
    for source, target in mapping:
        value = filters.get(source)
        if source == "symbol" and value not in (None, ""):
            value = _normalize_symbol(str(value))
        _put_if_present(body, target, value)
    return body
