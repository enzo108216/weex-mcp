from __future__ import annotations

import logging
import secrets
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from . import __version__
from .adapters.account import AccountAdapter, WeexAccountRestAdapter
from .adapters.broker_oauth import BrokerOAuthAdapter, WeexBrokerOAuthAdapter
from .adapters.market import MarketPriceAdapter, WeexMarketRestAdapter
from .adapters.partner import PartnerAdapter, WeexPartnerRestAdapter
from .adapters.trade_guard import FailClosedTradeAdapter, TradeGuardAdapter
from .adapters.trade_rest import WeexTradeRestAdapter
from .approval_token import ApprovalTokenCodec
from .audit import AuditSink, FileAuditSink, LoggingAuditSink, make_fingerprint
from .compatibility import build_compatibility_manifest
from .credential_context import CredentialContext, CredentialError
from .execution_authorization import ExecutionAuthorizer, FailClosedExecutionAuthorizer
from .response import error_response, jsonrpc_error, jsonrpc_result, mcp_call_tool_result
from .schema_validation import SchemaValidationError, validate_tool_arguments
from .secret_provider import RequestSecretProvider, SecretProvider
from .settings import Settings
from .tenant_context import TenantContext
from .tool_registry import ToolDefinition, ToolRegistry, build_default_registry
from .tools.account import (
    get_balance,
    get_bills,
    get_commission_rate,
    get_conditional_orders,
    get_config,
    get_orders,
    get_position,
    get_positions,
    get_symbol_config,
    get_trades,
    get_transfer_records,
)
from .tools.broker_oauth import exchange_code_for_api_key, generate_authorization_url
from .tools.market import (
    get_advanced_klines,
    get_book_ticker,
    get_catalog,
    get_depth,
    get_exchange_info,
    get_funding_rate,
    get_klines,
    get_open_interest,
    get_recent_trades,
    get_server_time,
    get_symbol_price,
    get_ticker_24h,
    ping,
)
from .tools.partner import (
    get_affiliate_commission,
    get_affiliate_uids,
    get_channel_user_trade_and_asset,
    get_referral_assets,
    get_referral_deal_data,
    query_sub_channel_transactions,
    verify_referrals,
)
from .tools.trade import (
    confirm_cancel_order,
    confirm_leverage,
    confirm_margin_mode,
    confirm_mutation,
    confirm_order,
    not_sent_outcome_for_confirm,
    preview_cancel_order,
    preview_leverage,
    preview_margin_mode,
    preview_mutation,
    preview_order,
)

LEGACY_PROTOCOL_VERSIONS = ("2025-06-18", "2025-11-25")


def _route_approval_binding(*, host_kind: str, settings: Settings) -> dict[str, str]:
    return {
        "host_kind": host_kind,
        "route_fingerprint": make_fingerprint(
            "approval_route",
            settings.spot_base_url,
            settings.futures_base_url,
        ),
    }


class McpService:
    def __init__(
        self,
        *,
        settings: Settings,
        runtime_profile: str,
        registry: ToolRegistry,
        approval_codec: ApprovalTokenCodec,
        market_adapter: MarketPriceAdapter,
        broker_oauth_adapter: BrokerOAuthAdapter | None,
        account_adapter: AccountAdapter,
        partner_adapter: PartnerAdapter | None,
        trade_adapter: TradeGuardAdapter,
        execution_authorizer: ExecutionAuthorizer,
        audit_sink: AuditSink,
    ):
        self.settings = settings
        self.runtime_profile = runtime_profile
        self.registry = registry
        self.approval_codec = approval_codec
        self.market_adapter = market_adapter
        self.broker_oauth_adapter = broker_oauth_adapter
        self.account_adapter = account_adapter
        self.partner_adapter = partner_adapter
        self.trade_adapter = trade_adapter
        self.execution_authorizer = execution_authorizer
        self.audit_sink = audit_sink
        self.compatibility_manifest = build_compatibility_manifest(
            package_version=__version__,
            runtime_profile=runtime_profile,
            tools=self._effective_tools(),
        )

    def _effective_tools(self) -> list[dict[str, Any]]:
        return [
            tool
            for tool in self.registry.list_tools()
            if self.settings.trade_enabled or tool["_meta"]["com.weex/mcp"]["module"] != "trade"
        ]

    def _instructions(self) -> str:
        modules = {
            tool["_meta"]["com.weex/mcp"]["module"]
            for tool in self._effective_tools()
        }
        capabilities = [
            description
            for module, description in (
                ("broker", "Broker OAuth"),
                ("market", "public market data"),
                ("account", "read-only account queries"),
                ("partner", "read-only Partner queries"),
                ("trade", "stateless preview/confirm trading"),
            )
            if module in modules
        ]
        prefix = "WEEX MCP tools"
        if not capabilities:
            return f"{prefix} with no enabled tool capabilities."
        if len(capabilities) == 1:
            summary = capabilities[0]
        elif len(capabilities) == 2:
            summary = " and ".join(capabilities)
        else:
            summary = f"{', '.join(capabilities[:-1])}, and {capabilities[-1]}"
        return f"{prefix} for {summary}."

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
                    "instructions": self._instructions(),
                    "cacheScope": "public",
                    "ttlMs": 300_000,
                    "_meta": {
                        "io.modelcontextprotocol/serverInfo": {
                            "name": self.settings.server_name,
                            "version": self.settings.server_version,
                        },
                        "com.weex/mcp": {
                            "compatibility": self.compatibility_manifest,
                        },
                    },
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
                        "instructions": self._instructions(),
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
            tools = self._effective_tools()
            result: dict[str, Any] = {"tools": tools}
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
            tool = self.registry.get_tool(name)
            payload = self._call_tool(params, context=active_context)
            allow_sensitive_success = bool(
                tool is not None and tool.allow_sensitive_success and payload.get("ok") is True
            )
            return self._jsonrpc_result(
                request_id,
                mcp_call_tool_result(payload, redact=not allow_sensitive_success),
                protocol_version=active_protocol_version,
                redact=not allow_sensitive_success,
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
            return self._with_confirm_not_sent(
                name,
                {},
                error_response("invalid_arguments", "Tool arguments must be an object."),
            )
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
                self._with_confirm_not_sent(
                    name,
                    arguments,
                    error_response(
                    "module_disabled",
                    "Trade module is disabled.",
                    audit=context.audit_dict() if context is not None else None,
                    ),
                ),
            )
        if tool.module in {"account", "partner", "trade"} and context is None:
            return self._finish_tool(
                tool,
                arguments,
                context,
                self._with_confirm_not_sent(
                    name,
                    arguments,
                    error_response("unauthorized", "Tenant context is required for this tool."),
                ),
            )

        try:
            validate_tool_arguments(arguments, tool.input_schema, defer_conditional=True)
        except SchemaValidationError as exc:
            return self._finish_tool(
                tool,
                arguments,
                context,
                self._with_confirm_not_sent(
                    name,
                    arguments,
                    error_response("invalid_arguments", str(exc), details=exc.details),
                ),
            )

        result = self._dispatch_tool(name, arguments, context)
        return self._finish_tool(tool, arguments, context, result)

    @staticmethod
    def _with_confirm_not_sent(
        name: Any,
        arguments: dict[str, Any],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        outcome = (
            not_sent_outcome_for_confirm(name, arguments, include_items=True)
            if isinstance(name, str)
            else None
        )
        if outcome is not None and "outcome" not in result:
            result = dict(result)
            result["outcome"] = outcome
        return result

    def _dispatch_tool(self, name: str, arguments: dict[str, Any], context: TenantContext | None) -> dict[str, Any]:
        if name == "broker_oauth_generate_authorization_url":
            if self.broker_oauth_adapter is None:
                return error_response("adapter_not_configured", "Broker OAuth adapter is not configured.")
            return generate_authorization_url(arguments, adapter=self.broker_oauth_adapter)
        if name == "broker_oauth_exchange_code_for_api_key":
            if self.broker_oauth_adapter is None:
                return error_response("adapter_not_configured", "Broker OAuth adapter is not configured.")
            return exchange_code_for_api_key(arguments, adapter=self.broker_oauth_adapter)
        if name == "market_get_symbol_price":
            return get_symbol_price(arguments, adapter=self.market_adapter)
        if name == "market_get_depth":
            return get_depth(arguments, adapter=self.market_adapter)
        if name == "market_get_klines":
            return get_klines(arguments, adapter=self.market_adapter)
        if name == "market_get_exchange_info":
            return get_exchange_info(arguments, adapter=self.market_adapter)
        if name == "market_get_server_time":
            return get_server_time(arguments, adapter=self.market_adapter)
        if name == "market_ping":
            return ping(arguments, adapter=self.market_adapter)
        if name == "market_get_ticker_24h":
            return get_ticker_24h(arguments, adapter=self.market_adapter)
        if name == "market_get_book_ticker":
            return get_book_ticker(arguments, adapter=self.market_adapter)
        if name == "market_get_recent_trades":
            return get_recent_trades(arguments, adapter=self.market_adapter)
        if name == "market_get_funding_rate":
            return get_funding_rate(arguments, adapter=self.market_adapter)
        if name == "market_get_open_interest":
            return get_open_interest(arguments, adapter=self.market_adapter)
        if name == "market_get_advanced_klines":
            return get_advanced_klines(arguments, adapter=self.market_adapter)
        if name == "market_get_catalog":
            return get_catalog(arguments, adapter=self.market_adapter)

        if context is None:
            return error_response("unauthorized", "Tenant context is required for this tool.")

        if name == "account_get_balance":
            return get_balance(arguments, context, adapter=self.account_adapter)
        if name == "account_get_positions":
            return get_positions(arguments, context, adapter=self.account_adapter)
        if name == "account_get_position":
            return get_position(arguments, context, adapter=self.account_adapter)
        if name == "account_get_orders":
            return get_orders(arguments, context, adapter=self.account_adapter)
        if name == "account_get_trades":
            return get_trades(arguments, context, adapter=self.account_adapter)
        if name == "account_get_bills":
            return get_bills(arguments, context, adapter=self.account_adapter)
        if name == "account_get_config":
            return get_config(arguments, context, adapter=self.account_adapter)
        if name == "account_get_commission_rate":
            return get_commission_rate(arguments, context, adapter=self.account_adapter)
        if name == "account_get_symbol_config":
            return get_symbol_config(arguments, context, adapter=self.account_adapter)
        if name == "account_get_conditional_orders":
            return get_conditional_orders(arguments, context, adapter=self.account_adapter)
        if name == "account_get_transfer_records":
            return get_transfer_records(arguments, context, adapter=self.account_adapter)
        if name.startswith("partner_"):
            if self.partner_adapter is None:
                return error_response("adapter_not_configured", "Partner adapter is not configured.")
            if name == "partner_get_affiliate_uids":
                return get_affiliate_uids(arguments, context, adapter=self.partner_adapter)
            if name == "partner_get_channel_user_trade_and_asset":
                return get_channel_user_trade_and_asset(arguments, context, adapter=self.partner_adapter)
            if name == "partner_get_affiliate_commission":
                return get_affiliate_commission(arguments, context, adapter=self.partner_adapter)
            if name == "partner_query_sub_channel_transactions":
                return query_sub_channel_transactions(arguments, context, adapter=self.partner_adapter)
            if name == "partner_verify_referrals":
                return verify_referrals(arguments, context, adapter=self.partner_adapter)
            if name == "partner_get_referral_assets":
                return get_referral_assets(arguments, context, adapter=self.partner_adapter)
            if name == "partner_get_referral_deal_data":
                return get_referral_deal_data(arguments, context, adapter=self.partner_adapter)
        if name == "trade_preview_order":
            return preview_order(arguments, context=context, trade_enabled=self.settings.trade_enabled, approval_codec=self.approval_codec, adapter=self.trade_adapter)
        if name == "trade_preview_cancel_order":
            return preview_cancel_order(arguments, context=context, trade_enabled=self.settings.trade_enabled, approval_codec=self.approval_codec, adapter=self.trade_adapter)
        if name == "trade_preview_leverage":
            return preview_leverage(arguments, context=context, trade_enabled=self.settings.trade_enabled, approval_codec=self.approval_codec, adapter=self.trade_adapter)
        if name == "trade_preview_margin_mode":
            return preview_margin_mode(arguments, context=context, trade_enabled=self.settings.trade_enabled, approval_codec=self.approval_codec, adapter=self.trade_adapter)
        if name == "trade_confirm_order":
            return confirm_order(arguments, context=context, trade_enabled=self.settings.trade_enabled, approval_codec=self.approval_codec, adapter=self.trade_adapter, execution_authorizer=self.execution_authorizer)
        if name == "trade_confirm_cancel_order":
            return confirm_cancel_order(arguments, context=context, trade_enabled=self.settings.trade_enabled, approval_codec=self.approval_codec, adapter=self.trade_adapter, execution_authorizer=self.execution_authorizer)
        if name == "trade_confirm_leverage":
            return confirm_leverage(arguments, context=context, trade_enabled=self.settings.trade_enabled, approval_codec=self.approval_codec, adapter=self.trade_adapter, execution_authorizer=self.execution_authorizer)
        if name == "trade_confirm_margin_mode":
            return confirm_margin_mode(arguments, context=context, trade_enabled=self.settings.trade_enabled, approval_codec=self.approval_codec, adapter=self.trade_adapter, execution_authorizer=self.execution_authorizer)
        for phase in ("trade_preview_", "trade_confirm_"):
            if not name.startswith(phase):
                continue
            action = name.removeprefix(phase)
            if action in {
                "batch_order",
                "batch_cancel",
                "cancel_scope",
                "conditional_order",
                "conditional_cancel",
                "tp_sl",
                "close_positions",
                "position_margin",
                "auto_append_margin",
            }:
                if phase == "trade_preview_":
                    return preview_mutation(
                        arguments,
                        action=action,
                        context=context,
                        trade_enabled=self.settings.trade_enabled,
                        approval_codec=self.approval_codec,
                        adapter=self.trade_adapter,
                    )
                return confirm_mutation(
                    arguments,
                    action=action,
                    context=context,
                    trade_enabled=self.settings.trade_enabled,
                    approval_codec=self.approval_codec,
                    adapter=self.trade_adapter,
                    execution_authorizer=self.execution_authorizer,
                )
        return error_response("unknown_tool", "Unknown tool.")

    def _finish_tool(self, tool: ToolDefinition, arguments: dict[str, Any], context: TenantContext | None, result: dict[str, Any]) -> dict[str, Any]:
        if tool.module not in {"account", "broker", "partner", "trade"}:
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
    runtime_profile: str = "default",
    registry: ToolRegistry | None = None,
    approval_binding: Mapping[str, Any] | None = None,
    market_adapter: MarketPriceAdapter | None = None,
    broker_oauth_adapter: BrokerOAuthAdapter | None = None,
    account_adapter: AccountAdapter | None = None,
    partner_adapter: PartnerAdapter | None = None,
    secret_provider: SecretProvider | None = None,
    trade_adapter: TradeGuardAdapter | None = None,
    execution_authorizer: ExecutionAuthorizer | None = None,
    audit_sink: AuditSink | None = None,
) -> McpService:
    if trade_enabled is not None and not isinstance(trade_enabled, bool):
        raise ValueError("trade_enabled must be a boolean.")
    if settings is None:
        settings = Settings(trade_enabled=bool(trade_enabled))
    elif trade_enabled is not None and trade_enabled != settings.trade_enabled:
        settings = replace(settings, trade_enabled=trade_enabled)
    active_registry = registry or build_default_registry()
    active_secret_provider = secret_provider or RequestSecretProvider()
    active_market_adapter: MarketPriceAdapter
    if market_adapter is not None:
        active_market_adapter = market_adapter
    else:
        active_market_adapter = WeexMarketRestAdapter(
            spot_base_url=settings.spot_base_url,
            futures_base_url=settings.futures_base_url,
            timeout_seconds=settings.market_timeout_seconds,
            locale=settings.request_locale,
            max_attempts=settings.market_max_attempts,
            retry_base_delay_seconds=settings.market_retry_base_delay_seconds,
        )
    active_trade_adapter = trade_adapter
    if active_trade_adapter is None:
        if settings.trade_enabled:
            active_trade_adapter = WeexTradeRestAdapter(
                secret_provider=active_secret_provider,
                spot_base_url=settings.spot_base_url,
                futures_base_url=settings.futures_base_url,
                timeout_seconds=settings.account_timeout_seconds,
                locale=settings.request_locale,
                market_adapter=active_market_adapter,
            )
        else:
            active_trade_adapter = FailClosedTradeAdapter()
    active_audit_sink = audit_sink
    if active_audit_sink is None:
        active_audit_sink = FileAuditSink(settings.audit_log_file) if settings.audit_log_file else LoggingAuditSink()
    active_broker_oauth_adapter = broker_oauth_adapter
    if active_broker_oauth_adapter is None and any(
        active_registry.has_tool(name)
        for name in (
            "broker_oauth_generate_authorization_url",
            "broker_oauth_exchange_code_for_api_key",
        )
    ):
        active_broker_oauth_adapter = WeexBrokerOAuthAdapter(timeout_seconds=settings.account_timeout_seconds)
    active_partner_adapter = partner_adapter
    if active_partner_adapter is None and any(
        tool["_meta"]["com.weex/mcp"]["module"] == "partner"
        for tool in active_registry.list_tools()
    ):
        active_partner_adapter = WeexPartnerRestAdapter(
            secret_provider=active_secret_provider,
            base_url=settings.spot_base_url,
            timeout_seconds=settings.partner_timeout_seconds,
            locale=settings.request_locale,
        )

    return McpService(
        settings=settings,
        runtime_profile=runtime_profile,
        registry=active_registry,
        approval_codec=ApprovalTokenCodec(
            secret=settings.approval_token_secret or secrets.token_urlsafe(48),
            ttl_seconds=settings.approval_token_ttl_seconds,
            binding=approval_binding,
        ),
        market_adapter=active_market_adapter,
        broker_oauth_adapter=active_broker_oauth_adapter,
        account_adapter=account_adapter
        or WeexAccountRestAdapter(
            secret_provider=active_secret_provider,
            spot_base_url=settings.spot_base_url,
            futures_base_url=settings.futures_base_url,
            timeout_seconds=settings.account_timeout_seconds,
            locale=settings.request_locale,
        ),
        partner_adapter=active_partner_adapter,
        trade_adapter=active_trade_adapter,
        execution_authorizer=execution_authorizer or FailClosedExecutionAuthorizer(),
        audit_sink=active_audit_sink,
    )
