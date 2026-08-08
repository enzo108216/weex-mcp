from __future__ import annotations

import logging
import secrets
from dataclasses import replace
from typing import Any

from .adapters.account import AccountAdapter, WeexAccountRestAdapter
from .adapters.market import MarketPriceAdapter, WeexMarketRestAdapter
from .adapters.trade_guard import FailClosedTradeAdapter, TradeGuardAdapter
from .adapters.trade_rest import WeexTradeRestAdapter
from .approval_token import ApprovalTokenCodec
from .audit import AuditSink, LoggingAuditSink
from .credential_context import CredentialContext, CredentialError
from .response import error_response, jsonrpc_error, jsonrpc_result, mcp_call_tool_result
from .schema_validation import SchemaValidationError, validate_tool_arguments
from .secret_provider import RequestSecretProvider, SecretProvider
from .settings import Settings
from .tenant_context import TenantContext
from .tool_registry import ToolDefinition, ToolRegistry, build_default_registry
from .tools.account import get_balance, get_bills, get_orders, get_positions, get_trades
from .tools.market import get_depth, get_exchange_info, get_klines, get_symbol_price
from .tools.trade import (
    confirm_cancel_order,
    confirm_leverage,
    confirm_margin_mode,
    confirm_order,
    preview_cancel_order,
    preview_leverage,
    preview_margin_mode,
    preview_order,
)

LEGACY_PROTOCOL_VERSIONS = ("2025-06-18", "2025-11-25")


class McpService:
    def __init__(
        self,
        *,
        settings: Settings,
        registry: ToolRegistry,
        approval_codec: ApprovalTokenCodec,
        market_adapter: MarketPriceAdapter,
        account_adapter: AccountAdapter,
        trade_adapter: TradeGuardAdapter,
        audit_sink: AuditSink,
    ):
        self.settings = settings
        self.registry = registry
        self.approval_codec = approval_codec
        self.market_adapter = market_adapter
        self.account_adapter = account_adapter
        self.trade_adapter = trade_adapter
        self.audit_sink = audit_sink

    def handle_jsonrpc(
        self,
        request: Any,
        *,
        context: TenantContext | None = None,
        protocol_version: str | None = None,
    ) -> dict[str, Any] | None:
        if not isinstance(request, dict):
            return jsonrpc_error(None, -32600, "Invalid JSON-RPC request.")

        request_id = request.get("id")
        is_notification = "id" not in request
        if not is_notification and (
            isinstance(request_id, bool) or not isinstance(request_id, (str, int))
        ):
            return jsonrpc_error(None, -32600, "Invalid JSON-RPC request.")
        if request.get("jsonrpc") != "2.0" or not isinstance(request.get("method"), str):
            return None if is_notification else jsonrpc_error(request_id, -32600, "Invalid JSON-RPC request.")

        method = str(request["method"])
        raw_params = request.get("params", {})
        params = {} if raw_params is None else raw_params
        if not isinstance(params, dict):
            return None if is_notification else jsonrpc_error(request_id, -32602, "JSON-RPC params must be an object.")

        active_protocol_version = protocol_version or self.settings.protocol_version
        requested_protocol_version = params.get("protocolVersion") if method == "initialize" else None
        if isinstance(requested_protocol_version, str) and requested_protocol_version in LEGACY_PROTOCOL_VERSIONS:
            active_protocol_version = requested_protocol_version
        is_legacy = active_protocol_version in LEGACY_PROTOCOL_VERSIONS

        # MCP notifications never receive a JSON-RPC response. In particular,
        # tools/call notifications are not executed, preventing unacknowledged
        # trade side effects.
        if is_notification:
            return None

        if method == "server/discover":
            return self._jsonrpc_result(
                request_id,
                {
                    "supportedVersions": [self.settings.protocol_version],
                    "capabilities": {"tools": {"listChanged": False}},
                    "instructions": "WEEX local MCP tools for public market data, read-only account queries, and stateless preview/confirm trading.",
                    "cacheScope": "public",
                    "ttlMs": 300_000,
                },
                protocol_version=active_protocol_version,
            )
        if method == "initialize":
            if is_legacy and requested_protocol_version == active_protocol_version:
                return self._jsonrpc_result(
                    request_id,
                    {
                        "protocolVersion": active_protocol_version,
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {
                            "name": self.settings.server_name,
                            "version": self.settings.server_version,
                        },
                        "instructions": "WEEX MCP tools for public market data, read-only account queries, and stateless preview/confirm trading.",
                    },
                    protocol_version=active_protocol_version,
                )
            return jsonrpc_error(
                request_id,
                -32601,
                f"initialize is not supported; use server/discover with protocol version {self.settings.protocol_version}.",
            )
        if method == "notifications/initialized":
            return self._jsonrpc_result(request_id, {}, protocol_version=active_protocol_version)
        if method == "tools/list":
            result: dict[str, Any] = {"tools": self.registry.list_tools()}
            if not is_legacy:
                result.update({"cacheScope": "public", "ttlMs": 300_000})
            return self._jsonrpc_result(
                request_id,
                result,
                protocol_version=active_protocol_version,
                redact=False,
            )
        if method == "tools/call":
            name = params.get("name")
            if not isinstance(name, str) or not self.registry.has_tool(name):
                return jsonrpc_error(request_id, -32602, "Unknown tool.")
            active_context = context or TenantContext(tenant_id="mcp", user_id="anonymous", session_id="request")
            return self._jsonrpc_result(
                request_id,
                mcp_call_tool_result(self._call_tool(params, context=active_context)),
                protocol_version=active_protocol_version,
            )
        return jsonrpc_error(request_id, -32601, f"Unsupported method: {method}")

    def _jsonrpc_result(
        self,
        request_id: Any,
        result: dict[str, Any],
        *,
        protocol_version: str,
        redact: bool = True,
    ) -> dict[str, Any]:
        response = jsonrpc_result(request_id, result, redact=redact)
        if protocol_version in LEGACY_PROTOCOL_VERSIONS:
            response["result"].pop("resultType", None)
        return response

    def _call_tool(self, params: dict[str, Any], *, context: TenantContext | None) -> dict[str, Any]:
        name = params.get("name")
        raw_arguments = params.get("arguments")
        if raw_arguments is None:
            arguments: dict[str, Any] = {}
        elif not isinstance(raw_arguments, dict):
            return error_response("invalid_arguments", "Tool arguments must be an object.")
        else:
            arguments = raw_arguments
        if not isinstance(name, str) or not self.registry.has_tool(name):
            return error_response("unknown_tool", "Unknown tool.")
        tool = self.registry.get_tool(name)
        if tool is None:
            return error_response("unknown_tool", "Unknown tool.")

        if tool.module == "trade" and not self.settings.trade_enabled:
            return self._finish_tool(
                tool,
                arguments,
                context,
                error_response(
                    "module_disabled",
                    "Trade module is disabled.",
                    audit=context.audit_dict() if context is not None else None,
                ),
            )
        if tool.module in {"account", "trade"} and context is None:
            return self._finish_tool(
                tool,
                arguments,
                context,
                error_response("unauthorized", "Tenant context is required for this tool."),
            )

        try:
            validate_tool_arguments(arguments, tool.input_schema, defer_conditional=True)
        except SchemaValidationError as exc:
            return self._finish_tool(tool, arguments, context, error_response("invalid_arguments", str(exc), details=exc.details))

        result = self._dispatch_tool(name, arguments, context)
        return self._finish_tool(tool, arguments, context, result)

    def _dispatch_tool(self, name: str, arguments: dict[str, Any], context: TenantContext | None) -> dict[str, Any]:
        if name == "market_get_symbol_price":
            return get_symbol_price(arguments, adapter=self.market_adapter)
        if name == "market_get_depth":
            return get_depth(arguments, adapter=self.market_adapter)
        if name == "market_get_klines":
            return get_klines(arguments, adapter=self.market_adapter)
        if name == "market_get_exchange_info":
            return get_exchange_info(arguments, adapter=self.market_adapter)

        if context is None:
            return error_response("unauthorized", "Tenant context is required for this tool.")

        if name == "account_get_balance":
            return get_balance(arguments, context, adapter=self.account_adapter)
        if name == "account_get_positions":
            return get_positions(arguments, context, adapter=self.account_adapter)
        if name == "account_get_orders":
            return get_orders(arguments, context, adapter=self.account_adapter)
        if name == "account_get_trades":
            return get_trades(arguments, context, adapter=self.account_adapter)
        if name == "account_get_bills":
            return get_bills(arguments, context, adapter=self.account_adapter)
        if name == "trade_preview_order":
            return preview_order(arguments, context=context, trade_enabled=self.settings.trade_enabled, approval_codec=self.approval_codec, adapter=self.trade_adapter)
        if name == "trade_preview_cancel_order":
            return preview_cancel_order(arguments, context=context, trade_enabled=self.settings.trade_enabled, approval_codec=self.approval_codec, adapter=self.trade_adapter)
        if name == "trade_preview_leverage":
            return preview_leverage(arguments, context=context, trade_enabled=self.settings.trade_enabled, approval_codec=self.approval_codec, adapter=self.trade_adapter)
        if name == "trade_preview_margin_mode":
            return preview_margin_mode(arguments, context=context, trade_enabled=self.settings.trade_enabled, approval_codec=self.approval_codec, adapter=self.trade_adapter)
        if name == "trade_confirm_order":
            return confirm_order(arguments, context=context, trade_enabled=self.settings.trade_enabled, approval_codec=self.approval_codec, adapter=self.trade_adapter)
        if name == "trade_confirm_cancel_order":
            return confirm_cancel_order(arguments, context=context, trade_enabled=self.settings.trade_enabled, approval_codec=self.approval_codec, adapter=self.trade_adapter)
        if name == "trade_confirm_leverage":
            return confirm_leverage(arguments, context=context, trade_enabled=self.settings.trade_enabled, approval_codec=self.approval_codec, adapter=self.trade_adapter)
        if name == "trade_confirm_margin_mode":
            return confirm_margin_mode(arguments, context=context, trade_enabled=self.settings.trade_enabled, approval_codec=self.approval_codec, adapter=self.trade_adapter)
        return error_response("unknown_tool", "Unknown tool.")

    def _finish_tool(self, tool: ToolDefinition, arguments: dict[str, Any], context: TenantContext | None, result: dict[str, Any]) -> dict[str, Any]:
        if tool.module not in {"account", "trade"}:
            return result
        event: dict[str, Any] = {
            "tool_name": tool.name,
            "module": tool.module,
            "permission": tool.permission,
            "success": bool(result.get("ok")),
        }
        if context is not None:
            event.update(context.audit_dict())
        if not result.get("ok") and isinstance(result.get("error"), dict):
            event["error_code"] = result["error"].get("code")
        try:
            credential = CredentialContext.from_argument(arguments.get("credential"))
        except CredentialError:
            credential = None
        if credential is not None:
            event.update(credential.safe_audit_dict())
        try:
            self.audit_sink.record(event)
        except Exception as exc:  # Audit plumbing must not leak secrets or alter tool results.
            logging.getLogger("weex_mcp.audit").warning("audit sink failed: %s", exc.__class__.__name__)
        return result

def build_default_service(
    *,
    settings: Settings | None = None,
    trade_enabled: bool | None = None,
    market_adapter: MarketPriceAdapter | None = None,
    account_adapter: AccountAdapter | None = None,
    secret_provider: SecretProvider | None = None,
    trade_adapter: TradeGuardAdapter | None = None,
    audit_sink: AuditSink | None = None,
) -> McpService:
    if settings is None:
        settings = Settings(trade_enabled=bool(trade_enabled))
    elif trade_enabled is not None and trade_enabled != settings.trade_enabled:
        settings = replace(settings, trade_enabled=trade_enabled)
    active_secret_provider = secret_provider or RequestSecretProvider()
    active_market_adapter = market_adapter or WeexMarketRestAdapter(timeout_seconds=settings.market_timeout_seconds)
    active_trade_adapter = trade_adapter
    if active_trade_adapter is None:
        if settings.trade_enabled:
            active_trade_adapter = WeexTradeRestAdapter(
                secret_provider=active_secret_provider,
                timeout_seconds=settings.account_timeout_seconds,
                market_adapter=active_market_adapter,
            )
        else:
            active_trade_adapter = FailClosedTradeAdapter()
    return McpService(
        settings=settings,
        registry=build_default_registry(),
        approval_codec=ApprovalTokenCodec(
            secret=settings.approval_token_secret or secrets.token_urlsafe(48),
            ttl_seconds=settings.approval_token_ttl_seconds,
        ),
        market_adapter=active_market_adapter,
        account_adapter=account_adapter or WeexAccountRestAdapter(secret_provider=active_secret_provider, timeout_seconds=settings.account_timeout_seconds),
        trade_adapter=active_trade_adapter,
        audit_sink=audit_sink or LoggingAuditSink(),
    )
