from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

_CANONICAL_NONNEGATIVE_INTEGER_PATTERN = r"^(?:0|[1-9][0-9]*)$"
_CANONICAL_POSITIVE_INTEGER_PATTERN = r"^[1-9][0-9]*$"
_LONG_MAX = 9_223_372_036_854_775_807
_INTEGER_MAX = 2_147_483_647
_DAY_MS = 24 * 60 * 60 * 1000
_FUTURES_ORDER_HISTORY_MAX_RANGE_MS = 90 * _DAY_MS
_FUTURES_INCOME_MAX_RANGE_MS = 100 * _DAY_MS
_FUTURES_TRADE_MAX_RANGE_MS = 7 * _DAY_MS
_DECIMAL_STRING_PATTERN = r"^\s*[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\s*$"
_OAUTH_SAFE_STRING_PATTERN = r"^[A-Za-z0-9._~-]+$"
_MODULE_NAMES = frozenset({"broker", "market", "account", "partner", "trade"})
_DEFAULT_MODULE_NAMES = frozenset({"broker", "market", "account", "trade"})
_CREDENTIAL_MODE_ORDER = ("credential_ref", "encrypted_envelope", "plaintext")
_CREDENTIAL_MODES = frozenset(_CREDENTIAL_MODE_ORDER)


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
        "preview_id": {"type": "string"},
        "expires_at": {"type": "integer", "minimum": 0},
        "summary": {"type": "object"},
        "risk": {"type": "object"},
        "user_confirmation": {"type": "object"},
        "clarification_required": {"type": "boolean"},
        "clarification_prompt": {"type": "string"},
        "outcome": {
            "type": "object",
            "required": ["class", "write_attempted", "action"],
            "properties": {
                "class": {
                    "type": "string",
                    "enum": ["not_sent", "definitive_rejection", "accepted", "unknown", "partial"],
                },
                "write_attempted": {"type": "boolean"},
                "action": {"type": "string", "minLength": 1},
                "client_order_id": {"type": "string", "minLength": 1},
                "operation_reference": {"type": "string", "minLength": 1},
                "reconcile_identity": {"type": "string", "minLength": 1},
                "items": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "required": ["class", "write_attempted", "action"],
                        "properties": {
                            "class": {
                                "type": "string",
                                "enum": ["not_sent", "definitive_rejection", "accepted", "unknown"],
                            },
                            "write_attempted": {"type": "boolean"},
                            "action": {"type": "string", "minLength": 1},
                            "client_order_id": {"type": "string", "minLength": 1},
                            "operation_reference": {"type": "string", "minLength": 1},
                            "reconcile_identity": {"type": "string", "minLength": 1},
                            "error_code": {
                                "type": "string",
                                "pattern": r"^[A-Za-z0-9_.:-]{1,64}$",
                            },
                        },
                        "anyOf": [
                            {"required": ["client_order_id"]},
                            {"required": ["operation_reference"]},
                            {"required": ["reconcile_identity"]},
                        ],
                        "additionalProperties": False,
                    },
                },
            },
            "additionalProperties": False,
        },
    },
    "additionalProperties": True,
}


_TOOL_TITLES = {
    "broker_oauth_generate_authorization_url": "Generate Broker OAuth authorization URL",
    "broker_oauth_exchange_code_for_api_key": "Exchange Broker OAuth code for API key",
    "market_get_symbol_price": "Get symbol price",
    "market_get_server_time": "Get server time",
    "market_ping": "Test Spot connectivity",
    "market_get_depth": "Get order book depth",
    "market_get_klines": "Get candlestick data",
    "market_get_exchange_info": "Get exchange information",
    "market_get_ticker_24h": "Get 24-hour ticker statistics",
    "market_get_book_ticker": "Get best bid and ask quotes",
    "market_get_recent_trades": "Get recent public trades",
    "market_get_funding_rate": "Get futures funding rates",
    "market_get_open_interest": "Get futures open interest",
    "market_get_advanced_klines": "Get advanced futures K-lines",
    "market_get_catalog": "Get WEEX market catalogs",
    "account_get_balance": "Get account balance",
    "account_get_positions": "Get futures positions",
    "account_get_position": "Get one futures position",
    "account_get_orders": "Get account orders",
    "account_get_trades": "Get account trades",
    "account_get_bills": "Get account bills",
    "account_get_config": "Get account configuration",
    "account_get_commission_rate": "Get futures commission rate",
    "account_get_symbol_config": "Get futures symbol configuration",
    "account_get_conditional_orders": "Get futures conditional orders",
    "account_get_transfer_records": "Get Spot transfer records",
    "partner_get_affiliate_uids": "Get affiliate UIDs",
    "partner_get_channel_user_trade_and_asset": "Get channel user trade and asset data",
    "partner_get_affiliate_commission": "Get affiliate commission records",
    "partner_query_sub_channel_transactions": "Query sub-channel transactions",
    "partner_verify_referrals": "Verify referrals",
    "partner_get_referral_assets": "Get referral assets",
    "partner_get_referral_deal_data": "Get referral deal data",
    "trade_preview_order": "Preview order",
    "trade_confirm_order": "Confirm order",
    "trade_preview_cancel_order": "Preview order cancellation",
    "trade_confirm_cancel_order": "Confirm order cancellation",
    "trade_preview_leverage": "Preview leverage update",
    "trade_confirm_leverage": "Confirm leverage update",
    "trade_preview_margin_mode": "Preview margin mode update",
    "trade_confirm_margin_mode": "Confirm margin mode update",
    "trade_preview_batch_order": "Preview batch order",
    "trade_confirm_batch_order": "Confirm batch order",
    "trade_preview_batch_cancel": "Preview batch cancellation",
    "trade_confirm_batch_cancel": "Confirm batch cancellation",
    "trade_preview_cancel_scope": "Preview scoped cancellation",
    "trade_confirm_cancel_scope": "Confirm scoped cancellation",
    "trade_preview_conditional_order": "Preview conditional order",
    "trade_confirm_conditional_order": "Confirm conditional order",
    "trade_preview_conditional_cancel": "Preview conditional cancellation",
    "trade_confirm_conditional_cancel": "Confirm conditional cancellation",
    "trade_preview_tp_sl": "Preview standalone TP/SL mutation",
    "trade_confirm_tp_sl": "Confirm standalone TP/SL mutation",
    "trade_preview_close_positions": "Preview position close",
    "trade_confirm_close_positions": "Confirm position close",
    "trade_preview_position_margin": "Preview position margin adjustment",
    "trade_confirm_position_margin": "Confirm position margin adjustment",
    "trade_preview_auto_append_margin": "Preview auto-append margin update",
    "trade_confirm_auto_append_margin": "Confirm auto-append margin update",
}


_FIELD_DESCRIPTIONS = {
    "client_id": "WEEX Broker client identifier registered for the merchant.",
    "client_secret": "WEEX Broker client secret used only to sign the token exchange request.",
    "redirect_uri": "Registered OAuth callback URI; the token exchange must use the exact authorization value.",
    "oauth_security": "Complete caller-supplied OAuth state and PKCE group; omit the whole object for MCP generation.",
    "state": "Per-authorization anti-CSRF state value managed and later verified by the merchant.",
    "code_verifier": "PKCE verifier retained by the merchant and submitted during token exchange.",
    "code_challenge": "Base64URL-encoded SHA-256 digest of the PKCE code_verifier, without padding.",
    "code": "Authorization code returned to the merchant callback by WEEX.",
    "market": "WEEX market family: spot or futures.",
    "trading_mode": "Account or trading environment: live or demo.",
    "symbol": "Instrument symbol in the WEEX catalog, for example BTCUSDT.",
    "symbols": "Non-empty list of unique WEEX instrument symbols.",
    "symbol_status": "Spot symbol status filter: TRADING, HALT, or BREAK.",
    "price_type": "Futures price source: INDEX or MARK.",
    "interval": "K-line interval supported by the selected market.",
    "limit": "Maximum number of records requested, encoded as a decimal string.",
    "credential": (
        "Request-scoped WEEX credential. The default runtime path is plaintext; "
        "credential_ref and encrypted_envelope require a custom host resolver."
    ),
    "status": "Order query mode: open, history, or single.",
    "order_id": "WEEX order identifier.",
    "client_order_id": "Stable caller-supplied order identifier; maps to WEEX newClientOrderId for new orders.",
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
    "from_type": "Source account type filter for Spot transfer records.",
    "after": "Return records created after this Unix millisecond timestamp.",
    "before": "Return records created before this Unix millisecond timestamp.",
    "mode": "Operation mode selected by this typed tool.",
    "kind": "Typed endpoint variant selected by this tool.",
    "catalog": "Catalog family to return: coins or API-tradable symbols.",
    "side": "Order side: BUY or SELL.",
    "order_type": "Order type: MARKET or LIMIT.",
    "quantity": "Positive order quantity as a decimal string.",
    "price": "Positive limit price as a decimal string.",
    "position_side": "Futures position side: LONG or SHORT; maps to WEEX positionSide.",
    "time_in_force": "Limit order execution policy supported by the selected market.",
    "tp_trigger_price": "Optional take-profit trigger price.",
    "sl_trigger_price": "Optional stop-loss trigger price.",
    "tp_working_type": "Take-profit trigger source: MARK_PRICE or CONTRACT_PRICE.",
    "sl_working_type": "Stop-loss trigger source: MARK_PRICE or CONTRACT_PRICE.",
    "preview_id": "Short-lived signed confirmation credential returned by the matching trade preview.",
    "approval_token": "Deprecated alias for preview_id accepted during client migration.",
    "confirmation": "Explicit confirmation object; accepted must be true.",
    "margin_mode": "Futures margin mode: cross or isolated.",
    "leverage": "Requested futures leverage as a positive decimal string.",
    "cross_leverage": "Explicit cross leverage as a positive decimal string.",
    "isolated_long_leverage": "Explicit isolated long leverage as a positive decimal string.",
    "isolated_short_leverage": "Explicit isolated short leverage as a positive decimal string.",
    "separated_type": "Futures margin mode API representation.",
    "orders": "Bound list of typed orders included in this batch request.",
    "order_ids": "Unique WEEX order IDs included in this batch cancellation.",
    "client_order_ids": "Unique caller order IDs included in this batch cancellation; maps to WEEX origClientOrderIds/origClientOrderIdList.",
    "order_kind": "Order family affected by a scoped cancellation: normal or conditional.",
    "scope": "Explicit mutation scope: one position/symbol or the whole account.",
    "acknowledge_all_symbols": "MCP-only acknowledgement required for an account-wide Futures cancellation.",
    "conditional_type": "Futures conditional order type.",
    "trigger_price": "Positive price that triggers the conditional or TP/SL action.",
    "client_algo_id": "Stable caller-supplied identifier for a new conditional or TP/SL order; maps to WEEX clientAlgoId.",
    "preset_take_profit_price": "Optional take-profit price attached to a conditional order.",
    "preset_stop_loss_price": "Optional stop-loss price attached to a conditional order.",
    "operation": "Typed operation performed by a combined preview/confirm tool.",
    "plan_type": "Standalone plan type: TAKE_PROFIT or STOP_LOSS.",
    "execute_price": "Execution price; zero or omission selects market execution where supported.",
    "trigger_price_type": "Trigger source: CONTRACT_PRICE or MARK_PRICE; maps to WEEX triggerPriceType.",
    "acknowledge_full_position": "Explicit acknowledgement required when TP/SL covers the full position.",
    "position_id": "WEEX futures position identifier.",
    "acknowledge_all_positions": "MCP-only acknowledgement required before closing all account positions.",
    "isolated_position_id": "WEEX isolated futures position identifier; maps to isolatedPositionId.",
    "amount": "Positive isolated-margin adjustment amount.",
    "adjustment": "Position-margin adjustment direction; maps to WEEX type=1 for increase and type=2 for decrease.",
    "enabled": "Whether isolated auto-append margin should be enabled; maps to WEEX autoAppendMargin.",
    "uid": "Optional invited-user UID as a canonical positive signed 64-bit decimal string.",
    "user_id": "Required direct-user UID as a canonical positive signed 64-bit decimal string.",
    "user_ids": "Non-empty list of canonical positive signed 64-bit UID strings.",
    "sub_uid": "Optional sub-affiliate UID as a canonical positive signed 64-bit decimal string.",
    "page_num": "Partner POST page number starting from 1, encoded as a decimal string.",
    "page_size": "Partner page size from 1 through 100, encoded as a decimal string.",
    "coin": "Partner commission coin filter: USDT or BTC.",
    "product_type": "Partner product family: SPOT or FUTURES.",
    "expected_environment": "Optional Partner environment binding: partner_production or partner_test.",
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
    is_confirmation = name.startswith("trade_confirm_")
    return {
        "readOnlyHint": not is_confirmation,
        "destructiveHint": is_confirmation,
        "idempotentHint": not is_confirmation,
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


def _nonnegative_decimal_string() -> dict[str, Any]:
    return {
        "type": "string",
        "minLength": 1,
        "pattern": _DECIMAL_STRING_PATTERN,
        "x-weex-nonnegativeDecimal": True,
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
    allow_sensitive_success: bool = False

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


def _projection_values(
    values: Iterable[str] | None,
    *,
    supported: frozenset[str],
    field: str,
) -> frozenset[str]:
    if values is None:
        return supported
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{field} must be a non-empty iterable of strings.")
    try:
        selected = frozenset(values)
    except TypeError:
        raise ValueError(f"{field} must be a non-empty iterable of strings.") from None
    if not selected:
        raise ValueError(f"{field} must not be empty.")
    if any(not isinstance(value, str) for value in selected):
        raise ValueError(f"{field} must contain only strings.")
    unknown = sorted(selected - supported)
    if unknown:
        raise ValueError(f"{field} contains unsupported values: {', '.join(unknown)}.")
    return selected


def build_default_registry(
    *,
    enabled_modules: Iterable[str] | None = None,
    credential_modes: Iterable[str] | None = None,
    allow_delegated_authorization: bool = False,
) -> ToolRegistry:
    if not isinstance(allow_delegated_authorization, bool):
        raise ValueError("allow_delegated_authorization must be a boolean.")
    active_modules = (
        _DEFAULT_MODULE_NAMES
        if enabled_modules is None
        else _projection_values(enabled_modules, supported=_MODULE_NAMES, field="enabled_modules")
    )
    active_credential_modes = _projection_values(
        credential_modes,
        supported=_CREDENTIAL_MODES,
        field="credential_modes",
    )
    oauth_security_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["state", "code_verifier", "code_challenge"],
        "properties": {
            "state": {
                "type": "string",
                "minLength": 22,
                "maxLength": 128,
                "pattern": _OAUTH_SAFE_STRING_PATTERN,
            },
            "code_verifier": {
                "type": "string",
                "minLength": 43,
                "maxLength": 128,
                "pattern": _OAUTH_SAFE_STRING_PATTERN,
            },
            "code_challenge": {
                "type": "string",
                "minLength": 43,
                "maxLength": 128,
                "pattern": _OAUTH_SAFE_STRING_PATTERN,
            },
        },
    }
    broker_oauth_authorization_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["client_id", "redirect_uri"],
        "properties": {
            "client_id": {"type": "string", "minLength": 1},
            "redirect_uri": {"type": "string", "minLength": 1},
            "oauth_security": oauth_security_schema,
        },
    }
    broker_oauth_exchange_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["client_id", "client_secret", "redirect_uri", "code", "code_verifier"],
        "properties": {
            "client_id": {"type": "string", "minLength": 1},
            "client_secret": {"type": "string", "minLength": 1},
            "redirect_uri": {"type": "string", "minLength": 1},
            "code": {"type": "string", "minLength": 1},
            "code_verifier": {
                "type": "string",
                "minLength": 43,
                "maxLength": 128,
                "pattern": _OAUTH_SAFE_STRING_PATTERN,
            },
        },
    }
    market_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["market"],
        "properties": {
            "market": {"type": "string", "enum": ["spot", "futures"]},
            "symbol": {"type": "string", "minLength": 1},
            "symbols": {
                "type": "array",
                "minItems": 1,
                "uniqueItems": True,
                "items": {"type": "string", "minLength": 1},
            },
            "price_type": {"type": "string", "minLength": 1},
        },
    }
    market_schema["oneOf"] = [
        {
            "type": "object",
            "required": ["market"],
            "properties": {"market": {"const": "spot"}},
            "allOf": [
                {"not": {"required": ["price_type"]}},
                {"not": {"required": ["symbol", "symbols"]}},
            ],
        },
        {
            "type": "object",
            "required": ["market", "symbol"],
            "properties": {
                "market": {"const": "futures"},
                "price_type": {"enum": ["INDEX", "MARK"]},
            },
            "allOf": [{"not": {"required": ["symbols"]}}],
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
            "symbols": {
                "type": "array",
                "minItems": 1,
                "uniqueItems": True,
                "items": {"type": "string", "minLength": 1},
            },
            "symbol_status": {"type": "string", "enum": ["TRADING", "HALT", "BREAK"]},
        },
        "oneOf": [
            {
                "required": ["market"],
                "properties": {"market": {"const": "spot"}},
                "allOf": [{"not": {"required": ["symbol", "symbols"]}}],
            },
            {
                "required": ["market"],
                "properties": {"market": {"const": "futures"}},
                "allOf": [
                    {"not": {"required": ["symbols"]}},
                    {"not": {"required": ["symbol_status"]}},
                ],
            },
        ],
    }
    market_server_time_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["market"],
        "properties": {"market": {"type": "string", "enum": ["spot", "futures"]}},
    }
    market_ping_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["market"],
        "properties": {"market": {"type": "string", "enum": ["spot"]}},
    }
    market_optional_symbol_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["market"],
        "properties": {
            "market": {"type": "string", "enum": ["spot", "futures"]},
            "symbol": {"type": "string", "minLength": 1},
            "symbols": {
                "type": "array",
                "minItems": 1,
                "uniqueItems": True,
                "items": {"type": "string", "minLength": 1},
            },
        },
        "oneOf": [
            {
                "properties": {"market": {"const": "spot"}},
                "required": ["market"],
                "allOf": [{"not": {"required": ["symbol", "symbols"]}}],
            },
            {
                "properties": {"market": {"const": "futures"}},
                "required": ["market"],
                "allOf": [{"not": {"required": ["symbols"]}}],
            },
        ],
    }
    market_recent_trades_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["market", "symbol"],
        "properties": {
            "market": {"type": "string", "enum": ["spot", "futures"]},
            "symbol": {"type": "string", "minLength": 1},
            "limit": _integer_string(minimum=1, maximum=1000),
        },
    }
    market_funding_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["market", "mode"],
        "properties": {
            "market": {"type": "string", "enum": ["futures"]},
            "mode": {"type": "string", "enum": ["current", "history"]},
            "symbol": {"type": "string", "minLength": 1},
            "start_time": _integer_string(minimum=0, maximum=_LONG_MAX),
            "end_time": _integer_string(minimum=0, maximum=_LONG_MAX),
            "limit": _integer_string(minimum=1, maximum=1000),
        },
        "oneOf": [
            {
                "required": ["mode"],
                "properties": {"mode": {"const": "current"}},
                "allOf": [{"not": {"required": [field]}} for field in ("start_time", "end_time", "limit")],
            },
            {
                "required": ["mode", "symbol"],
                "properties": {"mode": {"const": "history"}},
                "allOf": [
                    {"oneOf": [{"required": ["start_time", "end_time"]}, {"allOf": [{"not": {"required": ["start_time"]}}, {"not": {"required": ["end_time"]}}]}]},
                ],
                "x-weex-maxTimeRangeMs": 7 * _DAY_MS,
            },
        ],
    }
    market_open_interest_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["market", "symbol"],
        "properties": {
            "market": {"type": "string", "enum": ["futures"]},
            "symbol": {"type": "string", "minLength": 1},
        },
    }
    market_advanced_klines_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["market", "kind", "symbol", "interval"],
        "properties": {
            "market": {"type": "string", "enum": ["futures"]},
            "kind": {"type": "string", "enum": ["history", "index", "mark"]},
            "symbol": {"type": "string", "minLength": 1},
            "interval": {"type": "string", "enum": ["1m", "5m", "15m", "30m", "1h", "4h", "12h", "1d", "1w"]},
            "start_time": _integer_string(minimum=0, maximum=_LONG_MAX),
            "end_time": _integer_string(minimum=0, maximum=_LONG_MAX),
            "limit": _integer_string(minimum=1, maximum=1000),
            "price_type": {"type": "string", "enum": ["LAST", "INDEX", "MARK"]},
        },
        "oneOf": [
            {
                "required": ["kind"],
                "properties": {
                    "kind": {"const": "history"},
                    "limit": _integer_string(minimum=1, maximum=100),
                },
                "allOf": [
                    {"oneOf": [{"required": ["start_time", "end_time"]}, {"allOf": [{"not": {"required": ["start_time"]}}, {"not": {"required": ["end_time"]}}]}]},
                ],
            },
            {
                "required": ["kind"],
                "properties": {"kind": {"enum": ["index", "mark"]}},
                "allOf": [{"not": {"required": [field]}} for field in ("start_time", "end_time", "price_type")],
            },
        ],
    }
    market_catalog_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["market", "catalog"],
        "properties": {
            "market": {"type": "string", "enum": ["spot", "futures"]},
            "catalog": {"type": "string", "enum": ["coins", "symbols"]},
        },
        "oneOf": [
            {"properties": {"market": {"const": "spot"}, "catalog": {"enum": ["coins", "symbols"]}}, "required": ["market", "catalog"]},
            {"properties": {"market": {"const": "futures"}, "catalog": {"const": "symbols"}}, "required": ["market", "catalog"]},
        ],
    }
    credential_variants: dict[str, dict[str, Any]] = {
        "credential_ref": {
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
        "encrypted_envelope": {
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
        "plaintext": {
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
    }
    credential_properties: dict[str, dict[str, Any]] = {
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
    }
    selected_property_names = {"type", "permissions"}
    for mode in _CREDENTIAL_MODE_ORDER:
        if mode in active_credential_modes:
            selected_property_names.update(credential_variants[mode]["properties"])
    credential_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "oneOf": [
            credential_variants[mode]
            for mode in _CREDENTIAL_MODE_ORDER
            if mode in active_credential_modes
        ],
        "properties": {
            name: schema
            for name, schema in credential_properties.items()
            if name in selected_property_names
        },
    }
    partner_uid_schema = _integer_string(minimum=1, maximum=_LONG_MAX)
    partner_millisecond_schema = _integer_string(minimum=0, maximum=_LONG_MAX)
    partner_page_schema = {
        "type": "string",
        "pattern": _CANONICAL_POSITIVE_INTEGER_PATTERN,
        "x-weex-stringIntegerRange": {"minimum": 1},
    }
    partner_page_size_schema = _integer_string(minimum=1, maximum=100)
    partner_date_schema = {"type": "string", "pattern": r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"}
    partner_environment_schema = {
        "type": "string",
        "enum": ["partner_production", "partner_test"],
    }
    partner_common_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["credential"],
        "properties": {
            "credential": credential_schema,
            "uid": partner_uid_schema,
            "start_time": partner_millisecond_schema,
            "end_time": partner_millisecond_schema,
            "page": partner_page_schema,
            "page_size": partner_page_size_schema,
            "expected_environment": partner_environment_schema,
        },
        "allOf": [_optional_time_pair_constraint()],
    }
    partner_affiliate_uids_schema = deepcopy(partner_common_schema)
    partner_channel_trade_asset_schema = deepcopy(partner_common_schema)
    partner_affiliate_commission_schema = deepcopy(partner_common_schema)
    partner_affiliate_commission_schema["properties"].update(
        {
            "coin": {"type": "string", "enum": ["USDT", "BTC"]},
            "product_type": {"type": "string", "enum": ["SPOT", "FUTURES"]},
        }
    )
    nullable_partner_millisecond_schema = {
        "oneOf": [partner_millisecond_schema, {"enum": [None]}],
    }
    partner_sub_channel_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["credential", "product_type", "start_time", "end_time"],
        "properties": {
            "credential": credential_schema,
            "sub_uid": partner_uid_schema,
            "start_time": nullable_partner_millisecond_schema,
            "end_time": nullable_partner_millisecond_schema,
            "product_type": {"type": "string", "enum": ["SPOT", "FUTURES"]},
            "page_num": partner_page_schema,
            "page_size": partner_page_size_schema,
            "expected_environment": partner_environment_schema,
        },
        "oneOf": [
            {
                "required": ["product_type", "start_time", "end_time"],
                "properties": {
                    "product_type": {"enum": ["SPOT", "FUTURES"]},
                    "start_time": partner_millisecond_schema,
                    "end_time": partner_millisecond_schema,
                },
            },
            {
                "required": ["product_type", "start_time", "end_time", "expected_environment"],
                "properties": {
                    "product_type": {"enum": ["SPOT", "FUTURES"]},
                    "start_time": {"enum": [None]},
                    "end_time": {"enum": [None]},
                    "expected_environment": {"const": "partner_test"},
                },
            },
        ],
    }
    partner_verify_referrals_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["credential", "user_ids"],
        "properties": {
            "credential": credential_schema,
            "user_ids": {
                "type": "array",
                "minItems": 1,
                "maxItems": 100,
                "uniqueItems": True,
                "items": partner_uid_schema,
            },
            "expected_environment": partner_environment_schema,
        },
    }
    partner_referral_assets_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["credential", "user_id", "start_time", "end_time"],
        "properties": {
            "credential": credential_schema,
            "user_id": partner_uid_schema,
            "start_time": partner_date_schema,
            "end_time": partner_date_schema,
            "expected_environment": partner_environment_schema,
        },
    }
    partner_referral_deal_data_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["credential", "start_time", "end_time"],
        "properties": {
            "credential": credential_schema,
            "user_ids": {
                "type": "array",
                "minItems": 1,
                "items": partner_uid_schema,
            },
            "start_time": partner_date_schema,
            "end_time": partner_date_schema,
            "expected_environment": partner_environment_schema,
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
    account_position_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["market", "trading_mode", "symbol", "credential"],
        "properties": {
            "market": {"type": "string", "enum": ["futures"]},
            "trading_mode": {"type": "string", "enum": ["live"]},
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
    account_config_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["market", "trading_mode", "credential"],
        "properties": {
            "market": {"type": "string", "enum": ["spot", "futures"]},
            "trading_mode": {"type": "string", "enum": ["live"]},
            "credential": credential_schema,
        },
    }
    account_commission_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["market", "trading_mode", "symbol", "credential"],
        "properties": {
            "market": {"type": "string", "enum": ["futures"]},
            "trading_mode": {"type": "string", "enum": ["live"]},
            "symbol": {"type": "string", "minLength": 1},
            "credential": credential_schema,
        },
    }
    account_symbol_config_schema = deepcopy(account_commission_schema)
    account_symbol_config_schema["required"] = ["market", "trading_mode", "credential"]
    account_conditional_orders_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["market", "trading_mode", "status", "credential"],
        "properties": {
            "market": {"type": "string", "enum": ["futures"]},
            "trading_mode": {"type": "string", "enum": ["live"]},
            "status": {"type": "string", "enum": ["open", "history"]},
            "symbol": {"type": "string", "minLength": 1},
            "start_time": _integer_string(minimum=0, maximum=_LONG_MAX),
            "end_time": _integer_string(minimum=0, maximum=_LONG_MAX),
            "page": _integer_string(minimum=1, maximum=_INTEGER_MAX),
            "limit": _integer_string(minimum=1, maximum=1000),
            "credential": credential_schema,
        },
        "oneOf": [
            {
                "properties": {"status": {"const": "open"}, "limit": _integer_string(minimum=1, maximum=100)},
                "required": ["status"],
            },
            {
                "properties": {"status": {"const": "history"}, "limit": _integer_string(minimum=1, maximum=1000)},
                "required": ["status"],
                "allOf": [{"not": {"required": ["page"]}}],
                "x-weex-maxTimeRangeMs": _FUTURES_ORDER_HISTORY_MAX_RANGE_MS,
            },
        ],
    }
    account_conditional_orders_schema["oneOf"][1].setdefault("allOf", []).append(
        _optional_time_pair_constraint()
    )
    account_transfer_records_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["market", "trading_mode", "credential"],
        "properties": {
            "market": {"type": "string", "enum": ["spot"]},
            "trading_mode": {"type": "string", "enum": ["live"]},
            "coin_id": _integer_string(minimum=0, maximum=_INTEGER_MAX),
            "from_type": {"type": "string", "minLength": 1},
            "after": _integer_string(minimum=0, maximum=_LONG_MAX),
            "before": _integer_string(minimum=0, maximum=_LONG_MAX),
            "limit": _integer_string(minimum=1, maximum=_INTEGER_MAX),
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
        "required": ["market", "trading_mode", "symbol", "credential"],
        "properties": {
            "market": {"type": "string", "enum": ["futures"]},
            "trading_mode": {"type": "string", "enum": ["live"]},
            "symbol": {"type": "string", "minLength": 1},
            "margin_mode": {"type": "string", "enum": ["cross", "isolated", "CROSS", "CROSSED", "ISOLATED"]},
            "position_side": {"type": "string", "enum": ["LONG", "SHORT", "long", "short"]},
            "leverage": _positive_decimal_string(),
            "cross_leverage": _positive_decimal_string(),
            "isolated_long_leverage": _positive_decimal_string(),
            "isolated_short_leverage": _positive_decimal_string(),
            "credential": credential_schema,
        },
        "oneOf": [
            {
                "required": ["margin_mode", "leverage"],
                "allOf": [
                    {"not": {"required": [field]}}
                    for field in (
                        "cross_leverage",
                        "isolated_long_leverage",
                        "isolated_short_leverage",
                    )
                ],
            },
            {
                "anyOf": [
                    {"required": [field]}
                    for field in (
                        "cross_leverage",
                        "isolated_long_leverage",
                        "isolated_short_leverage",
                    )
                ],
                "allOf": [
                    {"not": {"required": ["leverage"]}},
                    {"not": {"required": ["position_side"]}},
                ],
            },
        ],
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
    client_id_schema = {"type": "string", "minLength": 1, "maxLength": 36, "pattern": r"^[.A-Za-z0-9_:/-]+$"}
    order_id_schema = _integer_string(minimum=1, maximum=_LONG_MAX)
    spot_batch_item: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["symbol", "side", "order_type", "quantity", "client_order_id"],
        "properties": {
            "symbol": {"type": "string", "minLength": 1},
            "side": {"type": "string", "enum": ["BUY", "SELL", "buy", "sell"]},
            "order_type": {"type": "string", "enum": ["MARKET", "LIMIT", "market", "limit"]},
            "quantity": _positive_decimal_string(),
            "price": _positive_decimal_string(),
            "time_in_force": {"type": "string", "enum": ["GTC", "IOC", "FOK", "gtc", "ioc", "fok"]},
            "client_order_id": client_id_schema,
        },
        "oneOf": [
            {"properties": {"order_type": {"enum": ["MARKET", "market"]}}, "required": ["order_type"], "allOf": [{"not": {"required": ["price"]}}, {"not": {"required": ["time_in_force"]}}]},
            {"properties": {"order_type": {"enum": ["LIMIT", "limit"]}}, "required": ["order_type", "price", "time_in_force"]},
        ],
    }
    futures_batch_item = deepcopy(spot_batch_item)
    futures_batch_item["required"].append("position_side")
    futures_batch_item["properties"]["position_side"] = {"type": "string", "enum": ["LONG", "SHORT", "long", "short"]}
    futures_batch_item["properties"]["time_in_force"]["enum"].extend(["POST_ONLY", "post_only"])
    for field in (
        "tp_trigger_price",
        "sl_trigger_price",
        "tp_working_type",
        "sl_working_type",
    ):
        futures_batch_item["properties"][field] = deepcopy(
            trade_preview_schema["properties"][field]
        )
    futures_batch_item["allOf"] = [
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
    trade_batch_order_preview_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["market", "trading_mode", "orders", "credential"],
        "properties": {
            "market": {"type": "string", "enum": ["spot", "futures"]},
            "trading_mode": {"type": "string", "enum": ["live"]},
            "orders": {"type": "array", "minItems": 1, "maxItems": 10, "items": {"oneOf": [spot_batch_item, futures_batch_item]}},
            "credential": credential_schema,
        },
        "oneOf": [
            {"properties": {"market": {"const": "spot"}, "orders": {"type": "array", "minItems": 1, "maxItems": 10, "items": spot_batch_item}}, "required": ["market"]},
            {"properties": {"market": {"const": "futures"}, "orders": {"type": "array", "minItems": 1, "maxItems": 5, "items": futures_batch_item}}, "required": ["market"]},
        ],
    }
    trade_batch_cancel_preview_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["market", "trading_mode", "credential"],
        "properties": {
            "market": {"type": "string", "enum": ["spot", "futures"]},
            "trading_mode": {"type": "string", "enum": ["live"]},
            "order_ids": {"type": "array", "minItems": 1, "maxItems": 10, "uniqueItems": True, "items": order_id_schema},
            "client_order_ids": {"type": "array", "minItems": 1, "maxItems": 10, "uniqueItems": True, "items": client_id_schema},
            "credential": credential_schema,
        },
        "oneOf": [
            {"required": ["order_ids"], "allOf": [{"not": {"required": ["client_order_ids"]}}]},
            {"required": ["client_order_ids"], "allOf": [{"not": {"required": ["order_ids"]}}]},
        ],
    }
    trade_cancel_scope_preview_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["market", "trading_mode", "order_kind", "scope", "credential"],
        "properties": {
            "market": {"type": "string", "enum": ["spot", "futures"]},
            "trading_mode": {"type": "string", "enum": ["live"]},
            "order_kind": {"type": "string", "enum": ["normal", "conditional"]},
            "scope": {"type": "string", "enum": ["symbol", "all"]},
            "symbol": {"type": "string", "minLength": 1},
            "acknowledge_all_symbols": {"type": "boolean", "const": True},
            "credential": credential_schema,
        },
        "oneOf": [
            {"properties": {"market": {"const": "spot"}, "order_kind": {"const": "normal"}, "scope": {"const": "symbol"}}, "required": ["market", "order_kind", "scope", "symbol"], "allOf": [{"not": {"required": ["acknowledge_all_symbols"]}}]},
            {"properties": {"market": {"const": "futures"}, "order_kind": {"enum": ["normal", "conditional"]}, "scope": {"const": "symbol"}}, "required": ["market", "order_kind", "scope", "symbol"], "allOf": [{"not": {"required": ["acknowledge_all_symbols"]}}]},
            {"properties": {"market": {"const": "futures"}, "order_kind": {"enum": ["normal", "conditional"]}, "scope": {"const": "all"}, "acknowledge_all_symbols": {"const": True}}, "required": ["market", "order_kind", "scope", "acknowledge_all_symbols"], "allOf": [{"not": {"required": ["symbol"]}}]},
        ],
    }
    trade_conditional_order_preview_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["market", "trading_mode", "symbol", "side", "position_side", "conditional_type", "quantity", "trigger_price", "client_algo_id", "credential"],
        "properties": {
            "market": {"type": "string", "enum": ["futures"]},
            "trading_mode": {"type": "string", "enum": ["live"]},
            "symbol": {"type": "string", "minLength": 1},
            "side": {"type": "string", "enum": ["BUY", "SELL", "buy", "sell"]},
            "position_side": {"type": "string", "enum": ["LONG", "SHORT", "long", "short"]},
            "conditional_type": {"type": "string", "enum": ["STOP", "TAKE_PROFIT", "STOP_MARKET", "TAKE_PROFIT_MARKET", "stop", "take_profit", "stop_market", "take_profit_market"]},
            "quantity": _positive_decimal_string(),
            "price": _positive_decimal_string(),
            "trigger_price": _positive_decimal_string(),
            "client_algo_id": client_id_schema,
            "preset_take_profit_price": _positive_decimal_string(),
            "preset_stop_loss_price": _positive_decimal_string(),
            "tp_working_type": {"type": "string", "enum": ["MARK_PRICE", "CONTRACT_PRICE", "mark_price", "contract_price"]},
            "sl_working_type": {"type": "string", "enum": ["MARK_PRICE", "CONTRACT_PRICE", "mark_price", "contract_price"]},
            "credential": credential_schema,
        },
        "oneOf": [
            {"properties": {"conditional_type": {"enum": ["STOP", "TAKE_PROFIT", "stop", "take_profit"]}}, "required": ["conditional_type", "price"]},
            {"properties": {"conditional_type": {"enum": ["STOP_MARKET", "TAKE_PROFIT_MARKET", "stop_market", "take_profit_market"]}}, "required": ["conditional_type"], "allOf": [{"not": {"required": ["price"]}}]},
        ],
    }
    trade_conditional_cancel_preview_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["market", "trading_mode", "order_id", "credential"],
        "properties": {
            "market": {"type": "string", "enum": ["futures"]},
            "trading_mode": {"type": "string", "enum": ["live"]},
            "order_id": order_id_schema,
            "credential": credential_schema,
        },
    }
    trade_tp_sl_preview_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["market", "trading_mode", "operation", "trigger_price", "credential"],
        "properties": {
            "market": {"type": "string", "enum": ["futures"]},
            "trading_mode": {"type": "string", "enum": ["live"]},
            "operation": {"type": "string", "enum": ["place", "modify"]},
            "symbol": {"type": "string", "minLength": 1},
            "client_algo_id": client_id_schema,
            "plan_type": {"type": "string", "enum": ["TAKE_PROFIT", "STOP_LOSS", "take_profit", "stop_loss"]},
            "trigger_price": _positive_decimal_string(),
            "execute_price": _nonnegative_decimal_string(),
            "quantity": _nonnegative_decimal_string(),
            "position_side": {"type": "string", "enum": ["LONG", "SHORT", "long", "short"]},
            "trigger_price_type": {"type": "string", "enum": ["MARK_PRICE", "CONTRACT_PRICE", "mark_price", "contract_price"]},
            "order_id": order_id_schema,
            "acknowledge_full_position": {"type": "boolean", "const": True},
            "credential": credential_schema,
        },
        "oneOf": [
            {
                "properties": {"operation": {"const": "place"}},
                "required": ["operation", "symbol", "client_algo_id", "plan_type", "position_side"],
                "allOf": [
                    {"not": {"required": ["order_id"]}},
                    {"oneOf": [
                        {"required": ["quantity"], "properties": {"quantity": _positive_decimal_string()}, "allOf": [{"not": {"required": ["acknowledge_full_position"]}}]},
                        {"required": ["acknowledge_full_position"], "properties": {"acknowledge_full_position": {"const": True}, "quantity": {"const": "0"}}},
                    ]},
                ],
            },
            {
                "properties": {"operation": {"const": "modify"}},
                "required": ["operation", "order_id"],
                "allOf": [{"not": {"required": [field]}} for field in ("symbol", "client_algo_id", "plan_type", "quantity", "position_side", "acknowledge_full_position")],
            },
        ],
    }
    trade_close_positions_preview_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["market", "trading_mode", "scope", "credential"],
        "properties": {
            "market": {"type": "string", "enum": ["futures"]},
            "trading_mode": {"type": "string", "enum": ["live"]},
            "scope": {"type": "string", "enum": ["position", "symbol", "all"]},
            "position_id": order_id_schema,
            "symbol": {"type": "string", "minLength": 1},
            "acknowledge_all_positions": {"type": "boolean", "const": True},
            "credential": credential_schema,
        },
        "oneOf": [
            {"properties": {"scope": {"const": "position"}}, "required": ["scope", "position_id"], "allOf": [{"not": {"required": ["acknowledge_all_positions"]}}]},
            {"properties": {"scope": {"const": "symbol"}}, "required": ["scope", "symbol"], "allOf": [{"not": {"required": ["position_id"]}}, {"not": {"required": ["acknowledge_all_positions"]}}]},
            {"properties": {"scope": {"const": "all"}, "acknowledge_all_positions": {"const": True}}, "required": ["scope", "acknowledge_all_positions"], "allOf": [{"not": {"required": ["position_id"]}}, {"not": {"required": ["symbol"]}}]},
        ],
    }
    trade_position_margin_preview_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["market", "trading_mode", "isolated_position_id", "amount", "adjustment", "credential"],
        "properties": {
            "market": {"type": "string", "enum": ["futures"]},
            "trading_mode": {"type": "string", "enum": ["live"]},
            "isolated_position_id": order_id_schema,
            "amount": _positive_decimal_string(),
            "adjustment": {"type": "string", "enum": ["increase", "decrease"]},
            "credential": credential_schema,
        },
    }
    trade_auto_append_margin_preview_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["market", "trading_mode", "position_id", "enabled", "credential"],
        "properties": {
            "market": {"type": "string", "enum": ["futures"]},
            "trading_mode": {"type": "string", "enum": ["live"]},
            "position_id": order_id_schema,
            "enabled": {"type": "boolean"},
            "credential": credential_schema,
        },
    }
    confirmation_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["accepted"],
        "properties": {
            "accepted": {"type": "boolean", "const": True},
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
            maximum_time_range_ms=_FUTURES_TRADE_MAX_RANGE_MS,
        ),
    ]
    account_trades_schema["oneOf"][1].setdefault("allOf", []).append(
        _optional_time_pair_constraint()
    )
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
    trade_leverage_preview_schema["x-weex-deferConditionalValidation"] = True
    trade_confirm_schema = _confirmation_schema(trade_preview_schema, confirmation_schema, allow_delegated_authorization=allow_delegated_authorization)
    trade_cancel_confirm_schema = _confirmation_schema(trade_cancel_preview_schema, confirmation_schema, allow_delegated_authorization=allow_delegated_authorization)
    trade_leverage_confirm_schema = _confirmation_schema(trade_leverage_preview_schema, confirmation_schema, allow_delegated_authorization=allow_delegated_authorization)
    trade_margin_mode_confirm_schema = _confirmation_schema(trade_margin_mode_preview_schema, confirmation_schema, allow_delegated_authorization=allow_delegated_authorization)
    trade_batch_order_confirm_schema = _confirmation_schema(trade_batch_order_preview_schema, confirmation_schema, allow_delegated_authorization=allow_delegated_authorization)
    trade_batch_cancel_confirm_schema = _confirmation_schema(trade_batch_cancel_preview_schema, confirmation_schema, allow_delegated_authorization=allow_delegated_authorization)
    trade_cancel_scope_confirm_schema = _confirmation_schema(trade_cancel_scope_preview_schema, confirmation_schema, allow_delegated_authorization=allow_delegated_authorization)
    trade_conditional_order_confirm_schema = _confirmation_schema(trade_conditional_order_preview_schema, confirmation_schema, allow_delegated_authorization=allow_delegated_authorization)
    trade_conditional_cancel_confirm_schema = _confirmation_schema(trade_conditional_cancel_preview_schema, confirmation_schema, allow_delegated_authorization=allow_delegated_authorization)
    trade_tp_sl_confirm_schema = _confirmation_schema(trade_tp_sl_preview_schema, confirmation_schema, allow_delegated_authorization=allow_delegated_authorization)
    trade_close_positions_confirm_schema = _confirmation_schema(trade_close_positions_preview_schema, confirmation_schema, allow_delegated_authorization=allow_delegated_authorization)
    trade_position_margin_confirm_schema = _confirmation_schema(trade_position_margin_preview_schema, confirmation_schema, allow_delegated_authorization=allow_delegated_authorization)
    trade_auto_append_margin_confirm_schema = _confirmation_schema(trade_auto_append_margin_preview_schema, confirmation_schema, allow_delegated_authorization=allow_delegated_authorization)
    for schema in (
        broker_oauth_authorization_schema,
        broker_oauth_exchange_schema,
        market_schema,
        market_depth_schema,
        market_klines_schema,
        market_exchange_info_schema,
        market_server_time_schema,
        market_ping_schema,
        market_optional_symbol_schema,
        market_recent_trades_schema,
        market_funding_schema,
        market_open_interest_schema,
        market_advanced_klines_schema,
        market_catalog_schema,
        partner_affiliate_uids_schema,
        partner_channel_trade_asset_schema,
        partner_affiliate_commission_schema,
        partner_sub_channel_schema,
        partner_verify_referrals_schema,
        partner_referral_assets_schema,
        partner_referral_deal_data_schema,
        account_schema,
        account_positions_schema,
        account_position_schema,
        account_orders_schema,
        account_trades_schema,
        account_bills_schema,
        account_config_schema,
        account_commission_schema,
        account_symbol_config_schema,
        account_conditional_orders_schema,
        account_transfer_records_schema,
        trade_preview_schema,
        trade_confirm_schema,
        trade_cancel_preview_schema,
        trade_leverage_preview_schema,
        trade_margin_mode_preview_schema,
        trade_cancel_confirm_schema,
        trade_leverage_confirm_schema,
        trade_margin_mode_confirm_schema,
        trade_batch_order_preview_schema,
        trade_batch_order_confirm_schema,
        trade_batch_cancel_preview_schema,
        trade_batch_cancel_confirm_schema,
        trade_cancel_scope_preview_schema,
        trade_cancel_scope_confirm_schema,
        trade_conditional_order_preview_schema,
        trade_conditional_order_confirm_schema,
        trade_conditional_cancel_preview_schema,
        trade_conditional_cancel_confirm_schema,
        trade_tp_sl_preview_schema,
        trade_tp_sl_confirm_schema,
        trade_close_positions_preview_schema,
        trade_close_positions_confirm_schema,
        trade_position_margin_preview_schema,
        trade_position_margin_confirm_schema,
        trade_auto_append_margin_preview_schema,
        trade_auto_append_margin_confirm_schema,
    ):
        _add_field_descriptions(schema)
    tool_definitions = [
            ToolDefinition(
                name="broker_oauth_generate_authorization_url",
                module="broker",
                permission="NONE",
                description="Generate a stateless WEEX Broker OAuth authorization URL and complete PKCE values.",
                input_schema=broker_oauth_authorization_schema,
                annotations={
                    "readOnlyHint": True,
                    "destructiveHint": False,
                    "idempotentHint": False,
                    "openWorldHint": True,
                },
                allow_sensitive_success=True,
            ),
            ToolDefinition(
                name="broker_oauth_exchange_code_for_api_key",
                module="broker",
                permission="CREATE_API_KEY",
                description="Exchange a WEEX Broker OAuth authorization code and create the user's API key.",
                input_schema=broker_oauth_exchange_schema,
                annotations={
                    "readOnlyHint": False,
                    "destructiveHint": True,
                    "idempotentHint": False,
                    "openWorldHint": True,
                },
                allow_sensitive_success=True,
            ),
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
                name="market_get_server_time",
                module="market",
                permission="NONE",
                description="Get the WEEX Spot or Futures server time. Public endpoint; no credential required.",
                input_schema=market_server_time_schema,
            ),
            ToolDefinition(
                name="market_ping",
                module="market",
                permission="NONE",
                description="Test WEEX Spot public API connectivity using the fixed ping endpoint.",
                input_schema=market_ping_schema,
            ),
            ToolDefinition(name="market_get_ticker_24h", module="market", permission="NONE", description="Get WEEX 24-hour ticker statistics.", input_schema=market_optional_symbol_schema),
            ToolDefinition(name="market_get_book_ticker", module="market", permission="NONE", description="Get WEEX best bid and ask quotes.", input_schema=market_optional_symbol_schema),
            ToolDefinition(name="market_get_recent_trades", module="market", permission="NONE", description="Get recent public WEEX trades.", input_schema=market_recent_trades_schema),
            ToolDefinition(name="market_get_funding_rate", module="market", permission="NONE", description="Get current or historical futures funding rates.", input_schema=market_funding_schema),
            ToolDefinition(name="market_get_open_interest", module="market", permission="NONE", description="Get current futures open interest.", input_schema=market_open_interest_schema),
            ToolDefinition(name="market_get_advanced_klines", module="market", permission="NONE", description="Get futures historical, index-price, or mark-price K-lines.", input_schema=market_advanced_klines_schema),
            ToolDefinition(name="market_get_catalog", module="market", permission="NONE", description="Get WEEX coin metadata or API-tradable symbol catalogs.", input_schema=market_catalog_schema),
            ToolDefinition(
                name="partner_get_affiliate_uids",
                module="partner",
                permission="USER_DATA",
                description="Get read-only WEEX Partner affiliate UID records.",
                input_schema=partner_affiliate_uids_schema,
            ),
            ToolDefinition(
                name="partner_get_channel_user_trade_and_asset",
                module="partner",
                permission="USER_DATA",
                description="Get read-only WEEX Partner channel-user trade and asset data.",
                input_schema=partner_channel_trade_asset_schema,
            ),
            ToolDefinition(
                name="partner_get_affiliate_commission",
                module="partner",
                permission="USER_DATA",
                description="Get read-only WEEX Partner affiliate commission records.",
                input_schema=partner_affiliate_commission_schema,
            ),
            ToolDefinition(
                name="partner_query_sub_channel_transactions",
                module="partner",
                permission="USER_DATA",
                description="Query read-only WEEX Partner sub-channel transaction statistics.",
                input_schema=partner_sub_channel_schema,
            ),
            ToolDefinition(
                name="partner_verify_referrals",
                module="partner",
                permission="USER_DATA",
                description="Verify read-only WEEX Partner referral relationships.",
                input_schema=partner_verify_referrals_schema,
            ),
            ToolDefinition(
                name="partner_get_referral_assets",
                module="partner",
                permission="USER_DATA",
                description="Get read-only WEEX Partner referral asset data.",
                input_schema=partner_referral_assets_schema,
            ),
            ToolDefinition(
                name="partner_get_referral_deal_data",
                module="partner",
                permission="USER_DATA",
                description="Get read-only WEEX Partner referral deal data.",
                input_schema=partner_referral_deal_data_schema,
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
                name="account_get_position",
                module="account",
                permission="USER_DATA",
                description="Get one live futures position using a request-level credential context.",
                input_schema=account_position_schema,
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
            ToolDefinition(name="account_get_config", module="account", permission="USER_DATA", description="Get read-only account configuration and capabilities.", input_schema=account_config_schema),
            ToolDefinition(name="account_get_commission_rate", module="account", permission="USER_DATA", description="Get futures maker and taker commission rates.", input_schema=account_commission_schema),
            ToolDefinition(name="account_get_symbol_config", module="account", permission="USER_DATA", description="Get futures account symbol configuration.", input_schema=account_symbol_config_schema),
            ToolDefinition(name="account_get_conditional_orders", module="account", permission="USER_DATA", description="Get open or historical futures conditional orders.", input_schema=account_conditional_orders_schema),
            ToolDefinition(name="account_get_transfer_records", module="account", permission="USER_DATA", description="Get read-only Spot account transfer history.", input_schema=account_transfer_records_schema),
            ToolDefinition(
                name="trade_preview_order",
                module="trade",
                permission="TRADE",
                description="Precheck an order and return a short-lived signed preview_id without server-side state.",
                input_schema=trade_preview_schema,
            ),
            ToolDefinition(
                name="trade_confirm_order",
                module="trade",
                permission="TRADE",
                description="Recheck a complete order request, validate its preview_id, then execute it.",
                input_schema=trade_confirm_schema,
            ),
            ToolDefinition(
                name="trade_preview_cancel_order",
                module="trade",
                permission="TRADE",
                description="Precheck an order cancellation and return a short-lived signed preview_id.",
                input_schema=trade_cancel_preview_schema,
            ),
            ToolDefinition(
                name="trade_confirm_cancel_order",
                module="trade",
                permission="TRADE",
                description="Recheck a complete cancellation request, validate its preview_id, then execute it.",
                input_schema=trade_cancel_confirm_schema,
            ),
            ToolDefinition(
                name="trade_preview_leverage",
                module="trade",
                permission="TRADE",
                description="Precheck a futures leverage update and return a short-lived signed preview_id.",
                input_schema=trade_leverage_preview_schema,
            ),
            ToolDefinition(
                name="trade_confirm_leverage",
                module="trade",
                permission="TRADE",
                description="Recheck a complete leverage request, validate its preview_id, then execute it.",
                input_schema=trade_leverage_confirm_schema,
            ),
            ToolDefinition(
                name="trade_preview_margin_mode",
                module="trade",
                permission="TRADE",
                description="Precheck a futures margin-mode update and return a short-lived signed preview_id.",
                input_schema=trade_margin_mode_preview_schema,
            ),
            ToolDefinition(
                name="trade_confirm_margin_mode",
                module="trade",
                permission="TRADE",
                description="Recheck a complete margin-mode request, validate its preview_id, then execute it.",
                input_schema=trade_margin_mode_confirm_schema,
            ),
            ToolDefinition(name="trade_preview_batch_order", module="trade", permission="TRADE", description="Precheck a Spot or Futures batch order without writing.", input_schema=trade_batch_order_preview_schema),
            ToolDefinition(name="trade_confirm_batch_order", module="trade", permission="TRADE", description="Recheck and submit a bound Spot or Futures batch order.", input_schema=trade_batch_order_confirm_schema),
            ToolDefinition(name="trade_preview_batch_cancel", module="trade", permission="TRADE", description="Precheck a batch order cancellation without writing.", input_schema=trade_batch_cancel_preview_schema),
            ToolDefinition(name="trade_confirm_batch_cancel", module="trade", permission="TRADE", description="Recheck and submit a bound batch cancellation.", input_schema=trade_batch_cancel_confirm_schema),
            ToolDefinition(name="trade_preview_cancel_scope", module="trade", permission="TRADE", description="Precheck symbol-wide or explicitly account-wide cancellation.", input_schema=trade_cancel_scope_preview_schema),
            ToolDefinition(name="trade_confirm_cancel_scope", module="trade", permission="TRADE", description="Recheck and submit a bound scope cancellation.", input_schema=trade_cancel_scope_confirm_schema),
            ToolDefinition(name="trade_preview_conditional_order", module="trade", permission="TRADE", description="Precheck a Futures conditional order without writing.", input_schema=trade_conditional_order_preview_schema),
            ToolDefinition(name="trade_confirm_conditional_order", module="trade", permission="TRADE", description="Recheck and submit a bound Futures conditional order.", input_schema=trade_conditional_order_confirm_schema),
            ToolDefinition(name="trade_preview_conditional_cancel", module="trade", permission="TRADE", description="Precheck a Futures conditional-order cancellation.", input_schema=trade_conditional_cancel_preview_schema),
            ToolDefinition(name="trade_confirm_conditional_cancel", module="trade", permission="TRADE", description="Recheck and submit a bound conditional-order cancellation.", input_schema=trade_conditional_cancel_confirm_schema),
            ToolDefinition(name="trade_preview_tp_sl", module="trade", permission="TRADE", description="Precheck a standalone Futures TP/SL placement or modification.", input_schema=trade_tp_sl_preview_schema),
            ToolDefinition(name="trade_confirm_tp_sl", module="trade", permission="TRADE", description="Recheck and submit a bound standalone Futures TP/SL mutation.", input_schema=trade_tp_sl_confirm_schema),
            ToolDefinition(name="trade_preview_close_positions", module="trade", permission="TRADE", description="Precheck an explicitly scoped Futures position close.", input_schema=trade_close_positions_preview_schema),
            ToolDefinition(name="trade_confirm_close_positions", module="trade", permission="TRADE", description="Recheck and submit a bound Futures position close.", input_schema=trade_close_positions_confirm_schema),
            ToolDefinition(name="trade_preview_position_margin", module="trade", permission="TRADE", description="Precheck an isolated-position margin adjustment.", input_schema=trade_position_margin_preview_schema),
            ToolDefinition(name="trade_confirm_position_margin", module="trade", permission="TRADE", description="Recheck and submit a bound isolated-position margin adjustment.", input_schema=trade_position_margin_confirm_schema),
            ToolDefinition(name="trade_preview_auto_append_margin", module="trade", permission="TRADE", description="Precheck an isolated-position auto-append margin update.", input_schema=trade_auto_append_margin_preview_schema),
            ToolDefinition(name="trade_confirm_auto_append_margin", module="trade", permission="TRADE", description="Recheck and submit a bound auto-append margin update.", input_schema=trade_auto_append_margin_confirm_schema),
        ]
    return ToolRegistry([tool for tool in tool_definitions if tool.module in active_modules])


def _confirmation_schema(
    base_schema: dict[str, Any],
    confirmation_schema: dict[str, Any],
    *,
    allow_delegated_authorization: bool,
) -> dict[str, Any]:
    schema = deepcopy(base_schema)
    schema["required"] = [*schema.get("required", [])]
    schema["properties"]["preview_id"] = {"type": "string", "minLength": 1}
    schema["properties"]["approval_token"] = {"type": "string", "minLength": 1}
    schema["properties"]["confirmation"] = deepcopy(confirmation_schema)
    if allow_delegated_authorization:
        schema["properties"]["delegated_authorization"] = {
            "type": "object",
            "additionalProperties": False,
            "required": ["ref"],
            "properties": {"ref": {"type": "string", "minLength": 1}},
        }
        schema.setdefault("allOf", []).append(
            {
                "oneOf": [
                    {
                        "required": ["confirmation"],
                        "not": {"required": ["delegated_authorization"]},
                    },
                    {
                        "required": ["delegated_authorization"],
                        "not": {"required": ["confirmation"]},
                    },
                ]
            }
        )
    else:
        schema["required"].append("confirmation")
    schema.setdefault("allOf", []).append(
        {
            "oneOf": [
                {
                    "required": ["preview_id"],
                    "not": {"required": ["approval_token"]},
                },
                {
                    "required": ["approval_token"],
                    "not": {"required": ["preview_id"]},
                },
            ]
        }
    )
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


def _optional_time_pair_constraint() -> dict[str, Any]:
    return {
        "oneOf": [
            {"required": ["start_time", "end_time"]},
            {
                "allOf": [
                    {"not": {"required": ["start_time"]}},
                    {"not": {"required": ["end_time"]}},
                ]
            },
        ]
    }
