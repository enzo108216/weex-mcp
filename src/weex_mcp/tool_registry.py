from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from .trade_execution import CONFIRMATION_REPLY_TEXT

_CANONICAL_NONNEGATIVE_INTEGER_PATTERN = r"^(?:0|[1-9][0-9]*)$"
_CANONICAL_POSITIVE_INTEGER_PATTERN = r"^[1-9][0-9]*$"
_LONG_MAX = 9_223_372_036_854_775_807
_INTEGER_MAX = 2_147_483_647
_DAY_MS = 24 * 60 * 60 * 1000
_FUTURES_ORDER_HISTORY_MAX_RANGE_MS = 90 * _DAY_MS
_FUTURES_INCOME_MAX_RANGE_MS = 100 * _DAY_MS
_DECIMAL_STRING_PATTERN = r"^\s*[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\s*$"


_MCP_TOOL_OUTPUT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "WEEX MCP tool result",
    "description": "Standard result envelope returned in structuredContent.",
    "type": "object",
    "required": ["ok"],
    "properties": {
        "ok": {"type": "boolean", "description": "Whether the tool completed successfully."},
        "data": {"description": "Tool-specific result data. The shape follows the selected endpoint."},
        "error": {
            "type": "object",
            "required": ["code", "message"],
            "properties": {
                "code": {"type": "string"},
                "message": {"type": "string"},
                "details": {},
            },
            "additionalProperties": True,
        },
        "audit": {"type": "object", "description": "Redacted request audit metadata, when applicable."},
        "request_id": {"type": "string"},
        "approval_token": {"type": "string"},
        "expires_at": {"type": "string"},
        "summary": {"type": "object"},
        "risk": {"type": "object"},
        "user_confirmation": {"type": "object"},
    },
    "additionalProperties": True,
}


_TOOL_TITLES = {
    "market_get_symbol_price": "Get symbol price",
    "market_get_depth": "Get order book depth",
    "market_get_klines": "Get candlestick data",
    "market_get_exchange_info": "Get exchange information",
    "account_get_balance": "Get account balance",
    "account_get_positions": "Get futures positions",
    "account_get_orders": "Get account orders",
    "account_get_trades": "Get account trades",
    "account_get_bills": "Get account bills",
    "trade_preview_order": "Preview order",
    "trade_confirm_order": "Confirm order",
    "trade_preview_cancel_order": "Preview order cancellation",
    "trade_confirm_cancel_order": "Confirm order cancellation",
    "trade_preview_leverage": "Preview leverage update",
    "trade_confirm_leverage": "Confirm leverage update",
    "trade_preview_margin_mode": "Preview margin mode update",
    "trade_confirm_margin_mode": "Confirm margin mode update",
}


_FIELD_DESCRIPTIONS = {
    "market": "WEEX market family: spot or futures.",
    "trading_mode": "Account or trading environment: live or demo.",
    "symbol": "Instrument symbol in the WEEX catalog, for example BTCUSDT.",
    "price_type": "Futures price source: INDEX or MARK.",
    "interval": "K-line interval supported by the selected market.",
    "limit": "Maximum number of records requested, encoded as a decimal string.",
    "credential": "Request-scoped WEEX credential: plaintext, credential_ref, or encrypted_envelope.",
    "status": "Order query mode: open, history, or single.",
    "order_id": "WEEX order identifier.",
    "client_order_id": "Stable caller-supplied order identifier for exchange-side correlation and retry reconciliation.",
    "start_time": "Inclusive start timestamp in Unix milliseconds.",
    "end_time": "Inclusive end timestamp in Unix milliseconds.",
    "page": "Endpoint-specific page number, encoded as a decimal string.",
    "bill_type": "Bill endpoint family: account, funding, tax, or income.",
    "coin_id": "Spot bill coin identifier, encoded as a decimal string.",
    "asset": "Asset or coin filter used by the selected account endpoint.",
    "biz_type": "Spot bill business type filter.",
    "income_type": "Futures income type filter.",
    "month": "Spot tax month in YYYY-MM format.",
    "next_key_id": "Futures income pagination cursor identifier.",
    "next_key_time": "Futures income pagination cursor timestamp.",
    "side": "Order side: BUY or SELL.",
    "order_type": "Order type: MARKET or LIMIT.",
    "quantity": "Positive order quantity as a decimal string.",
    "price": "Positive limit price as a decimal string.",
    "position_side": "Futures position side: LONG or SHORT.",
    "time_in_force": "Limit order execution policy supported by the selected market.",
    "tp_trigger_price": "Optional take-profit trigger price.",
    "sl_trigger_price": "Optional stop-loss trigger price.",
    "tp_working_type": "Take-profit trigger source: MARK_PRICE or CONTRACT_PRICE.",
    "sl_working_type": "Stop-loss trigger source: MARK_PRICE or CONTRACT_PRICE.",
    "approval_token": "Short-lived signed token returned by the matching stateless trade precheck.",
    "confirmation": "Explicit confirmation object; accepted must be true and reply_text must match the schema constant.",
    "margin_mode": "Futures margin mode: cross or isolated.",
    "leverage": "Requested futures leverage as a positive decimal string.",
    "separated_type": "Futures margin mode API representation.",
}


def _add_field_descriptions(schema: dict[str, Any]) -> None:
    """Add stable client-facing descriptions without changing validation rules."""
    for name, property_schema in schema.get("properties", {}).items():
        if isinstance(property_schema, dict):
            description = _FIELD_DESCRIPTIONS.get(name)
            if description:
                property_schema.setdefault("description", description)
            _add_field_descriptions(property_schema)
    for keyword in ("oneOf", "anyOf", "allOf"):
        for option in schema.get(keyword, []):
            if isinstance(option, dict):
                _add_field_descriptions(option)


def _default_annotations(*, module: str, name: str) -> dict[str, bool]:
    is_trade = module == "trade"
    is_confirmation = name.startswith("trade_confirm_")
    return {
        "readOnlyHint": not is_trade,
        "destructiveHint": is_confirmation,
        "idempotentHint": not is_trade,
        "openWorldHint": True,
    }


def _integer_string(*, minimum: int, maximum: int) -> dict[str, Any]:
    return {
        "type": "string",
        "pattern": _CANONICAL_NONNEGATIVE_INTEGER_PATTERN if minimum == 0 else _CANONICAL_POSITIVE_INTEGER_PATTERN,
        "x-weex-stringIntegerRange": {"minimum": minimum, "maximum": maximum},
    }


def _positive_decimal_string() -> dict[str, Any]:
    return {
        "type": "string",
        "minLength": 1,
        "pattern": _DECIMAL_STRING_PATTERN,
        "x-weex-positiveDecimal": True,
    }


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    module: str
    permission: str
    description: str
    input_schema: dict[str, Any]
    title: str = ""
    output_schema: dict[str, Any] | None = None
    annotations: dict[str, bool] | None = None

    def to_mcp_tool(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title or _TOOL_TITLES.get(self.name, self.name),
            "description": self.description,
            "inputSchema": self.input_schema,
            "outputSchema": self.output_schema or _MCP_TOOL_OUTPUT_SCHEMA,
            "annotations": self.annotations or _default_annotations(module=self.module, name=self.name),
            "_meta": {"com.weex/mcp": {"module": self.module, "permission": self.permission}},
        }


class ToolRegistry:
    def __init__(self, tools: list[ToolDefinition]):
        self._tools = {tool.name: tool for tool in tools}

    def list_tools(self) -> list[dict[str, Any]]:
        return [self._tools[name].to_mcp_tool() for name in sorted(self._tools)]

    def has_tool(self, name: str) -> bool:
        return name in self._tools

    def get_tool(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)


def build_default_registry() -> ToolRegistry:
    market_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["market", "symbol"],
        "properties": {
            "market": {"type": "string", "enum": ["spot", "futures"]},
            "symbol": {"type": "string", "minLength": 1},
            "price_type": {"type": "string", "minLength": 1},
        },
    }
    market_schema["oneOf"] = [
        {
            "type": "object",
            "required": ["market"],
            "properties": {"market": {"const": "spot"}},
            "allOf": [{"not": {"required": ["price_type"]}}],
        },
        {
            "type": "object",
            "required": ["market"],
            "properties": {
                "market": {"const": "futures"},
                "price_type": {"enum": ["INDEX", "MARK"]},
            },
        },
    ]
    market_depth_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["market", "symbol"],
        "properties": {
            "market": {"type": "string", "enum": ["spot", "futures"]},
            "symbol": {"type": "string", "minLength": 1},
            "limit": {"type": "string", "enum": ["15", "200"]},
        },
    }
    market_klines_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["market", "symbol", "interval"],
        "oneOf": [
            {
                "title": "Spot K-lines",
                "properties": {
                    "market": {"const": "spot"},
                    "interval": {"enum": ["1m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "1w"]},
                },
                "required": ["market"],
                "not": {"required": ["limit"]},
            },
            {
                "title": "Futures K-lines",
                "properties": {
                    "market": {"const": "futures"},
                    "interval": {"enum": ["1m", "5m", "15m", "30m", "1h", "4h", "12h", "1d", "1w"]},
                    "limit": _integer_string(minimum=1, maximum=1000),
                },
                "required": ["market"],
            },
        ],
        "properties": {
            "market": {"type": "string", "enum": ["spot", "futures"]},
            "symbol": {"type": "string", "minLength": 1},
            "interval": {"type": "string", "enum": ["1m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "1w"]},
            "limit": _integer_string(minimum=1, maximum=1000),
        },
    }
    market_exchange_info_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["market"],
        "properties": {
            "market": {"type": "string", "enum": ["spot", "futures"]},
            "symbol": {"type": "string", "minLength": 1},
        },
    }
    credential_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "oneOf": [
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["type", "key_id", "ref"],
                "properties": {
                    "type": {"const": "credential_ref"},
                    "key_id": {"type": "string", "minLength": 1},
                    "ref": {"type": "string", "minLength": 1},
                    "permissions": {"type": "array"},
                },
            },
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["type", "key_id", "ciphertext", "nonce", "alg"],
                "properties": {
                    "type": {"const": "encrypted_envelope"},
                    "key_id": {"type": "string", "minLength": 1},
                    "ciphertext": {"type": "string", "minLength": 1},
                    "nonce": {"type": "string", "minLength": 1},
                    "alg": {"type": "string", "minLength": 1},
                    "permissions": {"type": "array"},
                },
            },
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["type", "api_key", "api_secret", "api_passphrase"],
                "properties": {
                    "type": {"const": "plaintext"},
                    "api_key": {"type": "string", "minLength": 1},
                    "api_secret": {"type": "string", "minLength": 1},
                    "api_passphrase": {"type": "string", "minLength": 1},
                    "permissions": {"type": "array"},
                },
            },
        ],
        "properties": {
            "type": {"type": "string"},
            "key_id": {"type": "string", "minLength": 1},
            "ref": {"type": "string", "minLength": 1},
            "ciphertext": {"type": "string", "minLength": 1},
            "nonce": {"type": "string", "minLength": 1},
            "alg": {"type": "string", "minLength": 1},
            "api_key": {"type": "string", "minLength": 1},
            "api_secret": {"type": "string", "minLength": 1},
            "api_passphrase": {"type": "string", "minLength": 1},
            "permissions": {"type": "array"},
        },
    }
    account_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["market", "trading_mode", "credential"],
        "properties": {
            "market": {"type": "string", "enum": ["spot", "futures"]},
            "trading_mode": {"type": "string", "enum": ["live", "demo"]},
            "credential": credential_schema,
        },
    }
    account_positions_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["market", "trading_mode", "credential"],
        "properties": {
            "market": {"type": "string", "enum": ["spot", "futures"]},
            "trading_mode": {"type": "string", "enum": ["live", "demo"]},
            "symbol": {"type": "string", "minLength": 1},
            "credential": credential_schema,
        },
    }
    account_orders_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["market", "trading_mode", "status", "credential"],
        "properties": {
            "market": {"type": "string", "enum": ["spot", "futures"]},
            "trading_mode": {"type": "string", "enum": ["live", "demo"]},
            "status": {"type": "string", "enum": ["open", "history", "single"]},
            "symbol": {"type": "string", "minLength": 1},
            "order_id": _integer_string(minimum=1, maximum=_LONG_MAX),
            "client_order_id": {"type": "string", "minLength": 1},
            "start_time": _integer_string(minimum=0, maximum=_LONG_MAX),
            "end_time": _integer_string(minimum=0, maximum=_LONG_MAX),
            "limit": _integer_string(minimum=1, maximum=1000),
            "page": _integer_string(minimum=0, maximum=_INTEGER_MAX),
            "credential": credential_schema,
        },
    }
    account_trades_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["market", "trading_mode", "credential"],
        "properties": {
            "market": {"type": "string", "enum": ["spot", "futures"]},
            "trading_mode": {"type": "string", "enum": ["live", "demo"]},
            "symbol": {"type": "string", "minLength": 1},
            "order_id": _integer_string(minimum=1, maximum=_LONG_MAX),
            "start_time": _integer_string(minimum=0, maximum=_LONG_MAX),
            "end_time": _integer_string(minimum=0, maximum=_LONG_MAX),
            "limit": _integer_string(minimum=1, maximum=_INTEGER_MAX),
            "credential": credential_schema,
        },
    }
    account_bills_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["market", "trading_mode", "credential"],
        "properties": {
            "market": {"type": "string", "enum": ["spot", "futures"]},
            "trading_mode": {"type": "string", "enum": ["live", "demo"]},
            "bill_type": {"type": "string", "enum": ["account", "funding", "tax", "income"]},
            "coin_id": _integer_string(minimum=0, maximum=_INTEGER_MAX),
            "asset": {"type": "string", "minLength": 1},
            "biz_type": {"type": "string", "minLength": 1},
            "income_type": {"type": "string", "minLength": 1},
            "symbol": {"type": "string", "minLength": 1},
            "start_time": _integer_string(minimum=0, maximum=_LONG_MAX),
            "end_time": _integer_string(minimum=0, maximum=_LONG_MAX),
            "month": {"type": "string", "pattern": r"^[0-9]{4}-(?:0[1-9]|1[0-2])$"},
            "limit": _integer_string(minimum=1, maximum=200),
            "page": _integer_string(minimum=0, maximum=_INTEGER_MAX),
            "next_key_id": _integer_string(minimum=0, maximum=_LONG_MAX),
            "next_key_time": _integer_string(minimum=0, maximum=_LONG_MAX),
            "credential": credential_schema,
        },
    }
    trade_preview_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["market", "trading_mode", "symbol", "side", "order_type", "quantity", "client_order_id", "credential"],
        "properties": {
            "market": {"type": "string", "enum": ["spot", "futures"]},
            "trading_mode": {"type": "string", "enum": ["live", "demo"]},
            "symbol": {"type": "string", "minLength": 1},
            "side": {"type": "string", "enum": ["BUY", "SELL", "buy", "sell"]},
            "order_type": {"type": "string", "enum": ["MARKET", "LIMIT", "market", "limit"]},
            "quantity": _positive_decimal_string(),
            "price": _positive_decimal_string(),
            "position_side": {"type": "string", "enum": ["LONG", "SHORT", "long", "short"]},
            "client_order_id": {"type": "string", "minLength": 1, "maxLength": 36, "pattern": r"^[.A-Za-z0-9_:/-]+$"},
            "time_in_force": {"type": "string", "enum": ["GTC", "IOC", "FOK", "POST_ONLY", "gtc", "ioc", "fok", "post_only"]},
            "tp_trigger_price": _positive_decimal_string(),
            "sl_trigger_price": _positive_decimal_string(),
            "tp_working_type": {"type": "string", "enum": ["MARK_PRICE", "CONTRACT_PRICE", "mark_price", "contract_price"]},
            "sl_working_type": {"type": "string", "enum": ["MARK_PRICE", "CONTRACT_PRICE", "mark_price", "contract_price"]},
            "credential": credential_schema,
        },
    }
    trade_cancel_preview_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["market", "trading_mode", "credential"],
        "anyOf": [
            {"required": ["order_id"]},
            {"required": ["client_order_id"]},
        ],
        "properties": {
            "market": {"type": "string", "enum": ["spot", "futures"]},
            "trading_mode": {"type": "string", "enum": ["live"]},
            "order_id": {"type": "string", "minLength": 1, "maxLength": 19, "pattern": r"^[0-9]+$"},
            "client_order_id": {"type": "string", "minLength": 1, "maxLength": 36, "pattern": r"^[.A-Za-z0-9_:/-]+$"},
            "credential": credential_schema,
        },
    }
    trade_leverage_preview_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["market", "trading_mode", "symbol", "margin_mode", "leverage", "credential"],
        "properties": {
            "market": {"type": "string", "enum": ["futures"]},
            "trading_mode": {"type": "string", "enum": ["live"]},
            "symbol": {"type": "string", "minLength": 1},
            "margin_mode": {"type": "string", "enum": ["cross", "isolated", "CROSS", "CROSSED", "ISOLATED"]},
            "position_side": {"type": "string", "enum": ["LONG", "SHORT", "long", "short"]},
            "leverage": _positive_decimal_string(),
            "credential": credential_schema,
        },
    }
    trade_margin_mode_preview_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["market", "trading_mode", "symbol", "margin_mode", "credential"],
        "properties": {
            "market": {"type": "string", "enum": ["futures"]},
            "trading_mode": {"type": "string", "enum": ["live"]},
            "symbol": {"type": "string", "minLength": 1},
            "margin_mode": {"type": "string", "enum": ["cross", "isolated", "CROSS", "ISOLATED"]},
            "separated_type": {"type": "string", "enum": ["COMBINED", "SEPARATED", "combined", "separated"]},
            "credential": credential_schema,
        },
    }
    confirmation_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["accepted", "reply_text"],
        "properties": {
            "accepted": {"type": "boolean", "const": True},
            "reply_text": {"type": "string", "const": CONFIRMATION_REPLY_TEXT},
        },
    }

    account_schema["oneOf"] = [
        _mode_variant(market="spot", trading_mode="live"),
        _mode_variant(market="futures", trading_mode=("live", "demo")),
    ]
    account_positions_schema["properties"]["market"]["enum"] = ["futures"]
    account_orders_schema["oneOf"] = [
        _account_endpoint_variant(
            market="spot", trading_mode="live", status="open",
            allowed=("symbol",),
        ),
        _account_endpoint_variant(
            market="spot", trading_mode="live", status="history",
            required=("symbol",),
            allowed=("symbol", "start_time", "end_time", "limit", "page"),
            property_overrides={
                "limit": _integer_string(minimum=1, maximum=1000),
                "page": _integer_string(minimum=1, maximum=_INTEGER_MAX),
            },
        ),
        _account_endpoint_variant(
            market="spot", trading_mode="live", status="single",
            allowed=("order_id", "client_order_id"),
            any_of_required=(("order_id", "client_order_id"),),
        ),
        _account_endpoint_variant(
            market="futures", trading_mode="live", status="open",
            allowed=("symbol", "order_id", "start_time", "end_time", "limit", "page"),
            property_overrides={
                "limit": _integer_string(minimum=1, maximum=100),
                "page": _integer_string(minimum=0, maximum=_INTEGER_MAX),
            },
        ),
        _account_endpoint_variant(
            market="futures", trading_mode="live", status="history",
            allowed=("symbol", "start_time", "end_time", "limit", "page"),
            maximum_time_range_ms=_FUTURES_ORDER_HISTORY_MAX_RANGE_MS,
            property_overrides={
                "limit": _integer_string(minimum=1, maximum=1000),
                "page": _integer_string(minimum=0, maximum=_INTEGER_MAX),
            },
        ),
        _account_endpoint_variant(
            market="futures", trading_mode="live", status="single",
            required=("order_id",),
            allowed=("order_id",),
        ),
        _account_endpoint_variant(
            market="futures", trading_mode="demo", status="history",
            allowed=("symbol", "start_time", "end_time", "limit", "page"),
            maximum_time_range_ms=_FUTURES_ORDER_HISTORY_MAX_RANGE_MS,
            property_overrides={
                "limit": _integer_string(minimum=1, maximum=1000),
                "page": _integer_string(minimum=0, maximum=_INTEGER_MAX),
            },
        ),
    ]
    account_trades_schema["oneOf"] = [
        _account_endpoint_variant(
            market="spot", trading_mode="live", required=("symbol",),
            allowed=("symbol", "order_id", "start_time", "end_time", "limit"),
            property_overrides={"limit": _integer_string(minimum=1, maximum=_INTEGER_MAX)},
        ),
        _account_endpoint_variant(
            market="futures", trading_mode="live",
            allowed=("symbol", "order_id", "start_time", "end_time", "limit"),
            property_overrides={"limit": _integer_string(minimum=1, maximum=100)},
        ),
    ]
    spot_bills_variant = _mode_variant(market="spot", trading_mode="live")
    spot_bills_variant["allOf"] = [{
        "oneOf": [
            _account_endpoint_variant(
                market="spot", trading_mode="live", allowed=("coin_id", "biz_type", "start_time", "end_time", "limit"),
                forbidden=("bill_type", "asset", "income_type", "symbol", "month", "page", "next_key_id", "next_key_time"),
                property_overrides={"limit": _integer_string(minimum=1, maximum=100)},
            ),
            _account_endpoint_variant(
                market="spot", trading_mode="live", bill_type="account",
                allowed=("coin_id", "biz_type", "start_time", "end_time", "limit"),
                forbidden=("asset", "income_type", "symbol", "month", "page", "next_key_id", "next_key_time"),
                property_overrides={"limit": _integer_string(minimum=1, maximum=100)},
            ),
            _account_endpoint_variant(
                market="spot", trading_mode="live", bill_type="funding",
                allowed=("coin_id", "biz_type", "start_time", "end_time", "limit", "page"),
                forbidden=("asset", "income_type", "symbol", "month", "next_key_id", "next_key_time"),
                property_overrides={
                    "limit": _integer_string(minimum=1, maximum=100),
                    "page": _integer_string(minimum=1, maximum=_INTEGER_MAX),
                },
            ),
            _account_endpoint_variant(
                market="spot", trading_mode="live", bill_type="tax",
                allowed=("asset", "biz_type", "month", "limit", "page"),
                forbidden=("coin_id", "income_type", "symbol", "start_time", "end_time", "next_key_id", "next_key_time"),
                property_overrides={
                    "limit": _integer_string(minimum=1, maximum=200),
                    "page": _integer_string(minimum=1, maximum=_INTEGER_MAX),
                },
            ),
        ]
    }]
    futures_bills_variant = _mode_variant(market="futures", trading_mode="live")
    futures_bills_variant["allOf"] = [{
        "oneOf": [
            _account_endpoint_variant(
                market="futures", trading_mode="live", allowed=("asset", "symbol", "income_type", "start_time", "end_time", "limit", "next_key_id", "next_key_time"),
                forbidden=("bill_type", "coin_id", "biz_type", "month", "page"),
                maximum_time_range_ms=_FUTURES_INCOME_MAX_RANGE_MS,
                property_overrides={"limit": _integer_string(minimum=1, maximum=100)},
            ),
            _account_endpoint_variant(
                market="futures", trading_mode="live", bill_type="income",
                allowed=("asset", "symbol", "income_type", "start_time", "end_time", "limit", "next_key_id", "next_key_time"),
                forbidden=("coin_id", "biz_type", "month", "page"),
                maximum_time_range_ms=_FUTURES_INCOME_MAX_RANGE_MS,
                property_overrides={"limit": _integer_string(minimum=1, maximum=100)},
            ),
        ]
    }]
    account_bills_schema["oneOf"] = [spot_bills_variant, futures_bills_variant]
    spot_order_variant = _mode_variant(market="spot", trading_mode="live")
    # TP/SL is a futures-only order feature.  Keep the discriminator variants
    # in the public schema so clients can reject this before invoking an
    # adapter, while runtime validation remains the final enforcement point.
    spot_order_variant["allOf"] = [{"not": {"required": ["position_side"]}}] + [
        {"not": {"required": [field]}}
        for field in ("tp_trigger_price", "sl_trigger_price", "tp_working_type", "sl_working_type")
    ]
    futures_order_variants = []
    for trading_mode in ("live", "demo"):
        variant = _mode_variant(market="futures", trading_mode=trading_mode)
        variant["required"].append("position_side")
        futures_order_variants.append(variant)
    trade_preview_schema["oneOf"] = [
        spot_order_variant,
        *futures_order_variants,
    ]
    trade_preview_schema["allOf"] = [
        {
            "oneOf": [
                {"not": {"required": [working]}},
                {"required": [trigger, working]},
            ]
        }
        for trigger, working in (
            ("tp_trigger_price", "tp_working_type"),
            ("sl_trigger_price", "sl_working_type"),
        )
    ]
    trade_preview_schema["allOf"].append(
        {
            "oneOf": [
                {
                    "required": ["order_type"],
                    "properties": {"order_type": {"enum": ["MARKET", "market"]}},
                    "allOf": [
                        {"not": {"required": ["price"]}},
                        {"not": {"required": ["time_in_force"]}},
                    ],
                },
                {
                    "required": ["order_type", "price", "time_in_force"],
                    "properties": {"order_type": {"enum": ["LIMIT", "limit"]}},
                    "allOf": [{
                        "oneOf": [
                            {
                                "required": ["market", "time_in_force"],
                                "properties": {
                                    "market": {"const": "spot"},
                                    "time_in_force": {"enum": ["GTC", "IOC", "FOK", "gtc", "ioc", "fok"]},
                                },
                            },
                            {
                                "required": ["market", "time_in_force"],
                                "properties": {
                                    "market": {"const": "futures"},
                                    "time_in_force": {"enum": ["GTC", "IOC", "FOK", "POST_ONLY", "gtc", "ioc", "fok", "post_only"]},
                                },
                            },
                        ]
                    }],
                },
            ]
        }
    )
    trade_preview_schema["x-weex-deferConditionalValidation"] = True
    trade_confirm_schema = _confirmation_schema(trade_preview_schema, confirmation_schema)
    trade_cancel_confirm_schema = _confirmation_schema(trade_cancel_preview_schema, confirmation_schema)
    trade_leverage_confirm_schema = _confirmation_schema(trade_leverage_preview_schema, confirmation_schema)
    trade_margin_mode_confirm_schema = _confirmation_schema(trade_margin_mode_preview_schema, confirmation_schema)
    for schema in (
        market_schema,
        market_depth_schema,
        market_klines_schema,
        market_exchange_info_schema,
        account_schema,
        account_positions_schema,
        account_orders_schema,
        account_trades_schema,
        account_bills_schema,
        trade_preview_schema,
        trade_confirm_schema,
        trade_cancel_preview_schema,
        trade_leverage_preview_schema,
        trade_margin_mode_preview_schema,
        trade_cancel_confirm_schema,
        trade_leverage_confirm_schema,
        trade_margin_mode_confirm_schema,
    ):
        _add_field_descriptions(schema)
    return ToolRegistry(
        [
            ToolDefinition(
                name="market_get_symbol_price",
                module="market",
                permission="NONE",
                description="Get a WEEX market symbol price. Public endpoint; no credential required.",
                input_schema=market_schema,
            ),
            ToolDefinition(
                name="market_get_depth",
                module="market",
                permission="NONE",
                description="Get WEEX order book depth. Public endpoint; no credential required.",
                input_schema=market_depth_schema,
            ),
            ToolDefinition(
                name="market_get_klines",
                module="market",
                permission="NONE",
                description="Get WEEX candlestick/K-line data. Public endpoint; no credential required.",
                input_schema=market_klines_schema,
            ),
            ToolDefinition(
                name="market_get_exchange_info",
                module="market",
                permission="NONE",
                description="Get WEEX trading rules/exchange info. Public endpoint; no credential required.",
                input_schema=market_exchange_info_schema,
            ),
            ToolDefinition(
                name="account_get_balance",
                module="account",
                permission="USER_DATA",
                description="Get account balances using a request-level credential context.",
                input_schema=account_schema,
            ),
            ToolDefinition(
                name="account_get_positions",
                module="account",
                permission="USER_DATA",
                description="Get futures positions using a request-level credential context.",
                input_schema=account_positions_schema,
            ),
            ToolDefinition(
                name="account_get_orders",
                module="account",
                permission="USER_DATA",
                description="Get open, historical, or single order information using a request-level credential context.",
                input_schema=account_orders_schema,
            ),
            ToolDefinition(
                name="account_get_trades",
                module="account",
                permission="USER_DATA",
                description="Get user trade fills using a request-level credential context.",
                input_schema=account_trades_schema,
            ),
            ToolDefinition(
                name="account_get_bills",
                module="account",
                permission="USER_DATA",
                description="Get account bill/income records using a request-level credential context.",
                input_schema=account_bills_schema,
            ),
            ToolDefinition(
                name="trade_preview_order",
                module="trade",
                permission="TRADE",
                description="Precheck an order and return a short-lived signed approval_token without server-side state.",
                input_schema=trade_preview_schema,
            ),
            ToolDefinition(
                name="trade_confirm_order",
                module="trade",
                permission="TRADE",
                description="Recheck a complete order request, validate its approval_token, then execute it.",
                input_schema=trade_confirm_schema,
            ),
            ToolDefinition(
                name="trade_preview_cancel_order",
                module="trade",
                permission="TRADE",
                description="Precheck an order cancellation and return a short-lived signed approval_token.",
                input_schema=trade_cancel_preview_schema,
            ),
            ToolDefinition(
                name="trade_confirm_cancel_order",
                module="trade",
                permission="TRADE",
                description="Recheck a complete cancellation request, validate its approval_token, then execute it.",
                input_schema=trade_cancel_confirm_schema,
            ),
            ToolDefinition(
                name="trade_preview_leverage",
                module="trade",
                permission="TRADE",
                description="Precheck a futures leverage update and return a short-lived signed approval_token.",
                input_schema=trade_leverage_preview_schema,
            ),
            ToolDefinition(
                name="trade_confirm_leverage",
                module="trade",
                permission="TRADE",
                description="Recheck a complete leverage request, validate its approval_token, then execute it.",
                input_schema=trade_leverage_confirm_schema,
            ),
            ToolDefinition(
                name="trade_preview_margin_mode",
                module="trade",
                permission="TRADE",
                description="Precheck a futures margin-mode update and return a short-lived signed approval_token.",
                input_schema=trade_margin_mode_preview_schema,
            ),
            ToolDefinition(
                name="trade_confirm_margin_mode",
                module="trade",
                permission="TRADE",
                description="Recheck a complete margin-mode request, validate its approval_token, then execute it.",
                input_schema=trade_margin_mode_confirm_schema,
            ),
        ]
    )


def _confirmation_schema(base_schema: dict[str, Any], confirmation_schema: dict[str, Any]) -> dict[str, Any]:
    schema = deepcopy(base_schema)
    schema["required"] = [*schema.get("required", []), "approval_token", "confirmation"]
    schema["properties"]["approval_token"] = {"type": "string", "minLength": 1}
    schema["properties"]["confirmation"] = deepcopy(confirmation_schema)
    return schema


def _mode_variant(*, market: str, trading_mode: str | tuple[str, ...], status: str | None = None) -> dict[str, Any]:
    mode_schema: dict[str, Any]
    if isinstance(trading_mode, tuple):
        mode_schema = {"enum": list(trading_mode)}
    else:
        mode_schema = {"const": trading_mode}
    properties: dict[str, Any] = {
        "market": {"const": market},
        "trading_mode": mode_schema,
    }
    required = ["market", "trading_mode"]
    if status is not None:
        properties["status"] = {"const": status}
        required.append("status")
    return {"type": "object", "required": required, "properties": properties}


def _account_endpoint_variant(
    *,
    market: str,
    trading_mode: str,
    status: str | None = None,
    bill_type: str | None = None,
    required: tuple[str, ...] = (),
    allowed: tuple[str, ...] = (),
    forbidden: tuple[str, ...] = (),
    any_of_required: tuple[tuple[str, ...], ...] = (),
    property_overrides: dict[str, dict[str, Any]] | None = None,
    maximum_time_range_ms: int | None = None,
) -> dict[str, Any]:
    all_filter_fields = {
        "symbol", "order_id", "client_order_id", "start_time", "end_time", "limit", "page",
        "bill_type", "coin_id", "asset", "biz_type", "income_type", "month", "next_key_id", "next_key_time",
    }
    if not forbidden:
        forbidden = tuple(sorted(all_filter_fields - set(allowed)))
    properties: dict[str, Any] = {
        "market": {"const": market},
        "trading_mode": {"const": trading_mode},
    }
    properties.update(property_overrides or {})
    required_fields = ["market", "trading_mode", *required]
    if status is not None:
        properties["status"] = {"const": status}
        required_fields.append("status")
    if bill_type is not None:
        properties["bill_type"] = {"const": bill_type}
        required_fields.append("bill_type")
    constraints: list[dict[str, Any]] = [
        {"not": {"required": [field]}}
        for field in forbidden
    ]
    for alternatives in any_of_required:
        constraints.append({"anyOf": [{"required": [field]} for field in alternatives]})
    variant: dict[str, Any] = {
        "type": "object",
        "required": required_fields,
        "properties": properties,
    }
    if maximum_time_range_ms is not None:
        variant["x-weex-maxTimeRangeMs"] = maximum_time_range_ms
    if constraints:
        variant["allOf"] = constraints
    return variant
