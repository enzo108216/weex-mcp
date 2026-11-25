from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

from . import __version__
from .adapters.account import WeexAccountRestAdapter
from .adapters.market import WeexMarketRestAdapter
from .adapters.trade_rest import (
    TradeRequestBindingRequiresLiveState,
    build_trade_request_binding,
)
from .compatibility import build_compatibility_manifest
from .schema_validation import SchemaValidationError, validate_tool_arguments
from .secret_provider import ResolvedWeexCredential
from .settings import Settings
from .tenant_context import TenantContext
from .tool_registry import ToolRegistry, build_default_registry
from .tools import account as account_tools
from .tools import market as market_tools

_AUTH_HEADERS = {
    "ACCESS-KEY": "***",
    "ACCESS-PASSPHRASE": "***",
    "ACCESS-TIMESTAMP": "<generated-at-send>",
    "ACCESS-SIGN": "***",
}
_CREDENTIAL_PLACEHOLDER = {
    "type": "credential_ref",
    "key_id": "request-description",
    "ref": "request-description",
}
_MARKET_HANDLERS = {
    "market_get_symbol_price": market_tools.get_symbol_price,
    "market_get_depth": market_tools.get_depth,
    "market_get_klines": market_tools.get_klines,
    "market_get_exchange_info": market_tools.get_exchange_info,
    "market_get_server_time": market_tools.get_server_time,
    "market_ping": market_tools.ping,
    "market_get_ticker_24h": market_tools.get_ticker_24h,
    "market_get_book_ticker": market_tools.get_book_ticker,
    "market_get_recent_trades": market_tools.get_recent_trades,
    "market_get_funding_rate": market_tools.get_funding_rate,
    "market_get_open_interest": market_tools.get_open_interest,
    "market_get_advanced_klines": market_tools.get_advanced_klines,
    "market_get_catalog": market_tools.get_catalog,
}
_ACCOUNT_HANDLERS = {
    "account_get_balance": account_tools.get_balance,
    "account_get_positions": account_tools.get_positions,
    "account_get_position": account_tools.get_position,
    "account_get_orders": account_tools.get_orders,
    "account_get_trades": account_tools.get_trades,
    "account_get_bills": account_tools.get_bills,
    "account_get_config": account_tools.get_config,
    "account_get_commission_rate": account_tools.get_commission_rate,
    "account_get_symbol_config": account_tools.get_symbol_config,
    "account_get_conditional_orders": account_tools.get_conditional_orders,
    "account_get_transfer_records": account_tools.get_transfer_records,
}
_TRADE_ACTIONS = {
    "trade_preview_order": "order",
    "trade_preview_cancel_order": "cancel_order",
    "trade_preview_leverage": "update_leverage",
    "trade_preview_margin_mode": "change_margin_mode",
    "trade_preview_batch_order": "batch_order",
    "trade_preview_batch_cancel": "batch_cancel",
    "trade_preview_cancel_scope": "cancel_scope",
    "trade_preview_conditional_order": "conditional_order",
    "trade_preview_conditional_cancel": "conditional_cancel",
    "trade_preview_tp_sl": "tp_sl",
    "trade_preview_close_positions": "close_positions",
    "trade_preview_position_margin": "position_margin",
    "trade_preview_auto_append_margin": "auto_append_margin",
}


class RequestDescriptionError(ValueError):
    """Stable failure from the credential-free request-description contract."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class _CapturedDescription(Exception):
    def __init__(self, description: dict[str, Any]):
        super().__init__("request description captured")
        self.description = description


class _DescriptionSecretProvider:
    def resolve(
        self,
        *,
        credential_argument: dict[str, Any],
        context: TenantContext,
        required_permission: str,
    ) -> ResolvedWeexCredential:
        del credential_argument, context
        return ResolvedWeexCredential(
            key_id="request-description",
            api_key="",
            api_secret="",
            api_passphrase="",
            permissions=frozenset({required_permission}),
            fingerprint="request-description",
        )


class _DescribingAccountAdapter(WeexAccountRestAdapter):
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
        del credential
        base_url = self.spot_base_url if market == "spot" else self.futures_base_url
        raise _CapturedDescription(
            _request_description(
                method=method,
                base_url=base_url,
                path=path,
                query=query,
                body=body,
                locale=self.locale,
                authenticated=True,
                user_agent="weex-mcp/0.1 account-readonly",
            )
        )


class _CapturingAdapterProxy:
    def __init__(self, delegate: Any):
        self._delegate = delegate

    def __getattr__(self, name: str) -> Any:
        target = getattr(self._delegate, name)

        def capture(*args: Any, **kwargs: Any) -> dict[str, Any]:
            try:
                target(*args, **kwargs)
            except _CapturedDescription as exc:
                return exc.description
            raise RequestDescriptionError(
                "request_description_failed",
                "The MCP adapter did not produce a request description.",
            )

        return capture


def describe_request(
    *,
    settings: Settings,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Describe one typed Local request without credentials, signing, or transport."""

    if not isinstance(settings, Settings):
        raise RequestDescriptionError(
            "invalid_settings",
            "Request description requires WEEX MCP Settings.",
        )
    if not isinstance(name, str) or not name:
        raise RequestDescriptionError(
            "unknown_tool",
            "Request description requires a supported typed tool name.",
        )
    if not isinstance(arguments, dict):
        raise RequestDescriptionError(
            "invalid_arguments",
            "Request description arguments must be an object.",
        )
    if "credential" in arguments:
        raise RequestDescriptionError(
            "credential_forbidden",
            "Request description does not accept credential arguments.",
        )

    registry = _local_registry(trade_enabled=settings.trade_enabled)
    tool = registry.get_tool(name)
    if tool is None or name not in {*_MARKET_HANDLERS, *_ACCOUNT_HANDLERS, *_TRADE_ACTIONS}:
        raise RequestDescriptionError(
            "request_description_unsupported",
            "The selected tool is not available through the request-description API.",
        )
    if name in _TRADE_ACTIONS and not settings.trade_enabled:
        raise RequestDescriptionError(
            "module_disabled",
            "Trade request description requires trade_enabled settings.",
        )

    effective_arguments = dict(arguments)
    if name in _ACCOUNT_HANDLERS or name in _TRADE_ACTIONS:
        effective_arguments["credential"] = dict(_CREDENTIAL_PLACEHOLDER)
    try:
        validate_tool_arguments(
            effective_arguments,
            tool.input_schema,
            defer_conditional=True,
        )
    except SchemaValidationError as exc:
        raise RequestDescriptionError("invalid_arguments", str(exc)) from exc

    if name in _MARKET_HANDLERS:
        description = _describe_market(
            settings=settings,
            handler=_MARKET_HANDLERS[name],
            arguments=effective_arguments,
        )
    elif name in _ACCOUNT_HANDLERS:
        description = _describe_account(
            settings=settings,
            handler=_ACCOUNT_HANDLERS[name],
            arguments=effective_arguments,
        )
    else:
        description = _describe_trade(
            settings=settings,
            action=_TRADE_ACTIONS[name],
            arguments=effective_arguments,
        )

    description["_meta"] = {
        "com.weex/mcp": {
            "compatibility": build_compatibility_manifest(
                package_version=__version__,
                runtime_profile="local_user",
                tools=registry.list_tools(),
            )
        }
    }
    return description


def _local_registry(*, trade_enabled: bool) -> ToolRegistry:
    modules = {"market", "account", "partner"}
    if trade_enabled:
        modules.add("trade")
    return build_default_registry(
        enabled_modules=modules,
        credential_modes={"credential_ref"},
        allow_delegated_authorization=trade_enabled,
    )


def _describe_market(
    *,
    settings: Settings,
    handler: Any,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    def capture_transport(url: str, _timeout: float) -> bytes:
        parsed = urlsplit(url)
        query: dict[str, Any] = {}
        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            if key not in query:
                query[key] = value
            elif isinstance(query[key], list):
                query[key].append(value)
            else:
                query[key] = [query[key], value]
        raise _CapturedDescription(
            _request_description(
                method="GET",
                base_url=f"{parsed.scheme}://{parsed.netloc}",
                path=parsed.path,
                query=query,
                body={},
                locale=settings.request_locale,
                authenticated=False,
                user_agent="weex-mcp/0.1 public-market",
            )
        )

    adapter = _CapturingAdapterProxy(
        WeexMarketRestAdapter(
            spot_base_url=settings.spot_base_url,
            futures_base_url=settings.futures_base_url,
            timeout_seconds=settings.market_timeout_seconds,
            locale=settings.request_locale,
            transport=capture_transport,
            max_attempts=1,
        )
    )
    return _description_from_tool_result(handler(arguments, adapter=adapter))


def _describe_account(
    *,
    settings: Settings,
    handler: Any,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    adapter = _CapturingAdapterProxy(
        _DescribingAccountAdapter(
            secret_provider=_DescriptionSecretProvider(),
            spot_base_url=settings.spot_base_url,
            futures_base_url=settings.futures_base_url,
            timeout_seconds=settings.account_timeout_seconds,
            locale=settings.request_locale,
            transport=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("request-description transport must not run")
            ),
        )
    )
    result = handler(
        arguments,
        TenantContext(
            tenant_id="request-description",
            user_id="request-description",
            session_id="request-description",
        ),
        adapter=adapter,
    )
    return _description_from_tool_result(result)


def _describe_trade(
    *,
    settings: Settings,
    action: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    clean_arguments = {
        key: value for key, value in arguments.items() if key != "credential"
    }
    try:
        binding = build_trade_request_binding(action, clean_arguments)
    except TradeRequestBindingRequiresLiveState as exc:
        raise RequestDescriptionError(
            "description_requires_live_state",
            str(exc),
        ) from exc
    except ValueError as exc:
        raise RequestDescriptionError("invalid_arguments", str(exc)) from exc
    base_url = (
        settings.spot_base_url
        if binding["market"] == "spot"
        else settings.futures_base_url
    )
    return _request_description(
        method=binding["method"],
        base_url=base_url,
        path=binding["path"],
        query=binding["query"],
        body=binding["body"],
        locale=settings.request_locale,
        authenticated=True,
        user_agent="weex-mcp/0.1 trade",
    )


def _description_from_tool_result(result: dict[str, Any]) -> dict[str, Any]:
    if result.get("ok") is True and isinstance(result.get("data"), dict):
        return dict(result["data"])
    error = result.get("error")
    if isinstance(error, dict):
        code = error.get("code")
        message = error.get("message")
        if isinstance(code, str) and isinstance(message, str):
            raise RequestDescriptionError(code, message)
    raise RequestDescriptionError(
        "request_description_failed",
        "The MCP adapter did not produce a request description.",
    )


def _request_description(
    *,
    method: str,
    base_url: str,
    path: str,
    query: dict[str, Any],
    body: dict[str, Any],
    locale: str,
    authenticated: bool,
    user_agent: str,
) -> dict[str, Any]:
    query_string = urlencode(query, doseq=True)
    url = f"{base_url.rstrip('/')}{path}"
    if query_string:
        url = f"{url}?{query_string}"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "locale": locale,
        "User-Agent": user_agent,
    }
    if authenticated:
        headers.update(_AUTH_HEADERS)
    return {
        "method": method,
        "url": url,
        "headers": headers,
        "query": dict(query),
        "body": dict(body),
    }


__all__ = ["RequestDescriptionError", "describe_request"]
