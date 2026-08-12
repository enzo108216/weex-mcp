#!/usr/bin/env python3
"""Run an end-to-end WEEX futures-demo smoke through the local MCP service.

Credentials are read with getpass and only stored in this process. The private
transport has a hard allowlist so an accidental live Account/Trade request is
blocked before any network call.
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
import time
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from weex_mcp.adapters.account import WeexAccountRestAdapter
from weex_mcp.adapters.market import WeexMarketRestAdapter
from weex_mcp.adapters.trade_rest import WeexTradeRestAdapter
from weex_mcp.audit import MemoryAuditSink
from weex_mcp.secret_provider import RequestSecretProvider
from weex_mcp.service import McpService, build_default_service
from weex_mcp.settings import Settings
from weex_mcp.tenant_context import TenantContext

ALLOWED_DEMO_PRIVATE_REQUESTS = frozenset(
    {
        ("GET", "/capi/v3/account/accountConfig"),
        ("GET", "/capi/v3/sim/balance"),
        ("GET", "/capi/v3/sim/position/allPosition"),
        ("GET", "/capi/v3/sim/order/history"),
        ("POST", "/capi/v3/sim/order"),
    }
)


class SmokeFailure(RuntimeError):
    pass


class DemoOnlyPrivateTransport:
    """Prepared-request transport that blocks every non-demo private path."""

    allowed_requests = ALLOWED_DEMO_PRIVATE_REQUESTS

    def __init__(self, *, delegate: Callable[[dict[str, Any], float], bytes] | None = None):
        self._delegate = delegate or _send_prepared
        self.calls: list[tuple[str, str]] = []

    def __call__(self, prepared: dict[str, Any], timeout_seconds: float) -> bytes:
        method = str(prepared.get("method") or "").upper()
        path = urlsplit(str(prepared.get("url") or "")).path
        request_key = (method, path)
        if request_key not in self.allowed_requests:
            raise RuntimeError(f"blocked non-demo private request: {method} {path}")
        self.calls.append(request_key)
        return self._delegate(prepared, timeout_seconds)


def _send_prepared(prepared: dict[str, Any], timeout_seconds: float) -> bytes:
    request = Request(
        url=prepared["url"],
        method=prepared["method"],
        data=prepared.get("data"),
        headers=dict(prepared.get("headers") or {}),
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        return response.read()


def assert_secret_free(value: Any, secrets: Iterable[str]) -> None:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if any(secret and secret in encoded for secret in secrets):
        raise RuntimeError("credential material was found in the smoke report")


def _credential_argument(*, api_key: str, api_secret: str, api_passphrase: str) -> dict[str, Any]:
    return {
        "type": "plaintext",
        "api_key": api_key,
        "api_secret": api_secret,
        "api_passphrase": api_passphrase,
    }


def _call_tool(
    service: McpService,
    context: TenantContext,
    name: str,
    arguments: dict[str, Any],
    *,
    request_id: str | None = None,
) -> dict[str, Any]:
    response = service.handle_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": request_id or f"smoke_{uuid4().hex[:12]}",
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        context=context,
    )
    if not isinstance(response, dict) or not isinstance(response.get("result"), dict):
        raise SmokeFailure(f"{name} returned an invalid MCP response")
    return dict(response["result"])


def _expect_ok(result: dict[str, Any], *, tool: str) -> dict[str, Any]:
    if result.get("ok") is not True:
        error = result.get("error") if isinstance(result.get("error"), dict) else {}
        raise SmokeFailure(f"{tool} failed: {error.get('code', 'unknown')} - {error.get('message', 'unknown error')}")
    return result


def _expect_local_rejection(result: dict[str, Any], *, tool: str) -> str:
    if result.get("ok") is not False:
        raise SmokeFailure(f"{tool} unexpectedly succeeded for an unsupported demo capability")
    error = result.get("error") if isinstance(result.get("error"), dict) else {}
    code = str(error.get("code") or "")
    if code not in {"invalid_arguments", "missing_fields"}:
        raise SmokeFailure(f"{tool} returned unexpected rejection code: {code or 'missing'}")
    return code


def _data(result: dict[str, Any]) -> dict[str, Any]:
    value = result.get("data")
    return dict(value) if isinstance(value, dict) else {}


def _walk_mappings(value: Any):
    if isinstance(value, Mapping):
        yield value
        for item in value.values():
            yield from _walk_mappings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_mappings(item)


def _find_order(history_result: dict[str, Any], *, order_id: str, client_order_id: str | None) -> dict[str, Any] | None:
    aliases = {order_id}
    if client_order_id:
        aliases.add(client_order_id)
    for item in _walk_mappings(_data(history_result).get("orders")):
        identifiers = {
            str(item.get(key))
            for key in ("orderId", "order_id", "clientOrderId", "client_order_id", "newClientOrderId")
            if item.get(key) not in (None, "")
        }
        if aliases & identifiers:
            return dict(item)
    return None


def _order_status(order: Mapping[str, Any] | None) -> str:
    if order is None:
        return "NOT_FOUND"
    for key in ("status", "state", "orderStatus", "order_status"):
        if order.get(key) not in (None, ""):
            return str(order[key]).upper()
    return "UNKNOWN"


def _position_exposure(positions_result: dict[str, Any], *, base_symbol: str) -> list[dict[str, str]]:
    base = base_symbol.upper().replace("USDT", "")
    positions = _data(positions_result).get("positions")
    exposure: list[dict[str, str]] = []
    for item in _walk_mappings(positions):
        symbol = str(item.get("symbol") or item.get("contractCode") or "").upper()
        if base not in symbol or "USDT" not in symbol:
            continue
        quantity_key = next(
            (
                key
                for key in ("positionAmt", "positionAmount", "position", "quantity", "size", "total")
                if item.get(key) not in (None, "")
            ),
            None,
        )
        side = str(item.get("positionSide") or item.get("holdSide") or item.get("side") or "UNKNOWN").upper()
        exposure.append(
            {
                "symbol": symbol,
                "side": side,
                "quantity": str(item.get(quantity_key)) if quantity_key else "UNKNOWN",
            }
        )
    return sorted(exposure, key=lambda item: (item["symbol"], item["side"], item["quantity"]))


def _history_call(service: McpService, context: TenantContext, credential: dict[str, Any], *, demo_symbol: str) -> dict[str, Any]:
    return _expect_ok(
        _call_tool(
            service,
            context,
            "account_get_orders",
            {
                "market": "futures",
                "trading_mode": "demo",
                "status": "history",
                "symbol": demo_symbol,
                "limit": "20",
                "page": "0",
                "credential": credential,
            },
        ),
        tool="account_get_orders",
    )


def _wait_for_order(
    service: McpService,
    context: TenantContext,
    credential: dict[str, Any],
    *,
    demo_symbol: str,
    order_id: str,
    client_order_id: str | None,
    attempts: int = 12,
) -> dict[str, Any] | None:
    for attempt in range(attempts):
        history = _history_call(service, context, credential, demo_symbol=demo_symbol)
        found = _find_order(history, order_id=order_id, client_order_id=client_order_id)
        if found is not None:
            return found
        if attempt + 1 < attempts:
            time.sleep(0.5)
    return None


def _submit_demo_market_order(
    service: McpService,
    context: TenantContext,
    credential: dict[str, Any],
    *,
    symbol: str,
    side: str,
    position_side: str,
    quantity: str,
    label: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    client_order_id = f"mcpfull{label}{uuid4().hex[:12]}"
    order_arguments = {
        "market": "futures",
        "trading_mode": "demo",
        "symbol": symbol,
        "side": side,
        "position_side": position_side,
        "order_type": "MARKET",
        "quantity": quantity,
        "client_order_id": client_order_id,
        "credential": credential,
    }
    preview = _expect_ok(
        _call_tool(
            service,
            context,
            "trade_preview_order",
            order_arguments,
        ),
        tool="trade_preview_order",
    )
    confirmation = order_arguments | {
        "preview_id": preview["preview_id"],
        "confirmation": {"accepted": True, "reply_text": "确认"},
    }
    confirmed = _expect_ok(
        _call_tool(service, context, "trade_confirm_order", confirmation),
        tool="trade_confirm_order",
    )
    return preview, confirmed, confirmation


def run_full_smoke(*, api_key: str, api_secret: str, api_passphrase: str, symbol: str, quantity: str) -> dict[str, Any]:
    secrets = (api_key, api_secret, api_passphrase)
    if any(not value for value in secrets):
        raise SmokeFailure("all three credential values are required")

    provider = RequestSecretProvider()
    private_transport = DemoOnlyPrivateTransport()
    market_adapter = WeexMarketRestAdapter(timeout_seconds=10.0)
    account_adapter = WeexAccountRestAdapter(
        secret_provider=provider,
        timeout_seconds=10.0,
        transport=private_transport,
    )
    trade_adapter = WeexTradeRestAdapter(
        secret_provider=provider,
        timeout_seconds=10.0,
        transport=private_transport,
        market_adapter=market_adapter,
    )
    audit_sink = MemoryAuditSink()
    service = build_default_service(
        settings=Settings(trade_enabled=True, market_timeout_seconds=10.0, account_timeout_seconds=10.0),
        market_adapter=market_adapter,
        account_adapter=account_adapter,
        secret_provider=provider,
        trade_adapter=trade_adapter,
        audit_sink=audit_sink,
    )
    context = TenantContext(tenant_id="demo-smoke", user_id="demo-smoke", session_id=f"session-{uuid4().hex[:12]}")
    credential = _credential_argument(
        api_key=api_key,
        api_secret=api_secret,
        api_passphrase=api_passphrase,
    )
    normalized_symbol = symbol.upper().replace("-", "").replace("/", "")
    demo_symbol = normalized_symbol[:-4] + "SUSDT" if normalized_symbol.endswith("USDT") else normalized_symbol

    listed = service.handle_jsonrpc({"jsonrpc": "2.0", "id": "smoke-list", "method": "tools/list", "params": {}}, context=context)
    tool_names = [tool["name"] for tool in listed["result"]["tools"]]
    if len(tool_names) != 17:
        raise SmokeFailure(f"tools/list returned {len(tool_names)} tools instead of 17")

    market_results: dict[str, dict[str, Any]] = {}
    for market in ("spot", "futures"):
        calls = {
            "price": ("market_get_symbol_price", {"market": market, "symbol": normalized_symbol}),
            "depth": ("market_get_depth", {"market": market, "symbol": normalized_symbol, "limit": "15"}),
            "klines": (
                "market_get_klines",
                {"market": market, "symbol": normalized_symbol, "interval": "1m", **({"limit": "20"} if market == "futures" else {})},
            ),
            "exchange_info": ("market_get_exchange_info", {"market": market, "symbol": normalized_symbol}),
        }
        market_results[market] = {}
        for label, (tool, arguments) in calls.items():
            result = _expect_ok(_call_tool(service, context, tool, arguments), tool=tool)
            data = _data(result)
            market_results[market][label] = {
                "ok": True,
                "source": data.get("source"),
                "items": len(data.get("bids", [])) if label == "depth" else len(data.get("klines", [])) if label == "klines" else len(data.get("symbols", [])) if label == "exchange_info" else 1,
            }

    initial_balance = _expect_ok(
        _call_tool(service, context, "account_get_balance", {"market": "futures", "trading_mode": "demo", "credential": credential}),
        tool="account_get_balance",
    )
    initial_positions = _expect_ok(
        _call_tool(service, context, "account_get_positions", {"market": "futures", "trading_mode": "demo", "credential": credential}),
        tool="account_get_positions",
    )
    initial_history = _history_call(service, context, credential, demo_symbol=demo_symbol)
    initial_exposure = _position_exposure(initial_positions, base_symbol=normalized_symbol)

    unsupported_cases = [
        ("account_get_balance", {"market": "spot", "trading_mode": "demo", "credential": credential}),
        ("account_get_positions", {"market": "spot", "trading_mode": "demo", "credential": credential}),
        ("account_get_orders", {"market": "spot", "trading_mode": "demo", "status": "history", "symbol": normalized_symbol, "credential": credential}),
        ("account_get_trades", {"market": "futures", "trading_mode": "demo", "symbol": demo_symbol, "credential": credential}),
        ("account_get_bills", {"market": "futures", "trading_mode": "demo", "bill_type": "income", "credential": credential}),
        ("trade_preview_order", {"market": "spot", "trading_mode": "demo", "symbol": normalized_symbol, "side": "BUY", "order_type": "MARKET", "quantity": quantity, "client_order_id": "unsupported-spot-demo", "credential": credential}),
        ("trade_preview_cancel_order", {"market": "futures", "trading_mode": "demo", "symbol": normalized_symbol, "order_id": "unsupported-demo-order", "credential": credential}),
        ("trade_preview_leverage", {"market": "futures", "trading_mode": "demo", "symbol": normalized_symbol, "margin_mode": "cross", "leverage": "2", "credential": credential}),
        ("trade_preview_margin_mode", {"market": "futures", "trading_mode": "demo", "symbol": normalized_symbol, "margin_mode": "cross", "credential": credential}),
    ]
    unsupported_results = []
    for tool, arguments in unsupported_cases:
        call_count = len(private_transport.calls)
        code = _expect_local_rejection(_call_tool(service, context, tool, arguments), tool=tool)
        if len(private_transport.calls) != call_count:
            raise SmokeFailure(f"{tool} reached a private endpoint before rejecting unsupported demo capability")
        unsupported_results.append({"tool": tool, "error_code": code, "network_calls": 0})

    open_result: dict[str, Any] | None = None
    close_result: dict[str, Any] | None = None
    open_order: dict[str, Any] | None = None
    close_order: dict[str, Any] | None = None
    cleanup_error: str | None = None
    main_error: BaseException | None = None

    try:
        _, open_result, _ = _submit_demo_market_order(
            service,
            context,
            credential,
            symbol=normalized_symbol,
            side="BUY",
            position_side="LONG",
            quantity=quantity,
            label="open",
        )
        open_exchange = dict(open_result.get("result") or {})
        open_order_id = str(open_exchange.get("exchange_order_id") or "")
        if not open_order_id:
            raise SmokeFailure("open order result did not contain exchange_order_id")
        open_order = _wait_for_order(
            service,
            context,
            credential,
            demo_symbol=demo_symbol,
            order_id=open_order_id,
            client_order_id=str(open_exchange.get("client_order_id") or "") or None,
        )
        if open_order is None:
            raise SmokeFailure("open demo order was not found in order history")
    except BaseException as exc:
        main_error = exc
    finally:
        if open_result is not None:
            try:
                _, close_result, _ = _submit_demo_market_order(
                    service,
                    context,
                    credential,
                    symbol=normalized_symbol,
                    side="SELL",
                    position_side="LONG",
                    quantity=quantity,
                    label="close",
                )
                close_exchange = dict(close_result.get("result") or {})
                close_order_id = str(close_exchange.get("exchange_order_id") or "")
                if not close_order_id:
                    raise SmokeFailure("close order result did not contain exchange_order_id")
                close_order = _wait_for_order(
                    service,
                    context,
                    credential,
                    demo_symbol=demo_symbol,
                    order_id=close_order_id,
                    client_order_id=str(close_exchange.get("client_order_id") or "") or None,
                )
                if close_order is None:
                    raise SmokeFailure("close demo order was not found in order history")
            except BaseException as exc:
                cleanup_error = f"{exc.__class__.__name__}: {exc}"

    if main_error is not None:
        if cleanup_error:
            raise SmokeFailure(f"{main_error}; cleanup also failed: {cleanup_error}") from main_error
        raise main_error
    if cleanup_error:
        raise SmokeFailure(f"demo cleanup failed: {cleanup_error}")

    final_positions = initial_positions
    final_exposure = initial_exposure
    position_restored = False
    for attempt in range(12):
        final_positions = _expect_ok(
            _call_tool(service, context, "account_get_positions", {"market": "futures", "trading_mode": "demo", "credential": credential}),
            tool="account_get_positions final",
        )
        final_exposure = _position_exposure(final_positions, base_symbol=normalized_symbol)
        if final_exposure == initial_exposure:
            position_restored = True
            break
        if attempt < 11:
            time.sleep(0.5)
    if not position_restored:
        raise SmokeFailure("target symbol exposure did not return to the initial snapshot after cleanup")

    open_exchange = dict(open_result.get("result") or {})
    close_exchange = dict(close_result.get("result") or {})
    report = {
        "ok": True,
        "scope": "local_mcp_with_weex_futures_demo",
        "tools": {"count": len(tool_names), "stable": True},
        "market": market_results,
        "account": {
            "balance_items": len(_data(initial_balance).get("balances", [])),
            "initial_position_items": len(list(_walk_mappings(_data(initial_positions).get("positions")))),
            "initial_history_mappings": len(list(_walk_mappings(_data(initial_history).get("orders")))),
        },
        "unsupported_demo": unsupported_results,
        "trade": {
            "open": {
                "exchange_order_id": open_exchange.get("exchange_order_id"),
                "client_order_id": open_exchange.get("client_order_id"),
                "history_status": _order_status(open_order),
            },
            "stateless_confirmation": {
                "server_side_cache": False,
                "replay_tested": False,
                "client_order_id_required": True,
            },
            "cleanup": {
                "exchange_order_id": close_exchange.get("exchange_order_id"),
                "client_order_id": close_exchange.get("client_order_id"),
                "history_status": _order_status(close_order),
            },
            "position": {
                "before": initial_exposure,
                "after": final_exposure,
                "restored": position_restored,
            },
        },
        "private_requests": [{"method": method, "path": path} for method, path in private_transport.calls],
        "private_request_allowlist_enforced": all(call in ALLOWED_DEMO_PRIVATE_REQUESTS for call in private_transport.calls),
        "audit_events": len(audit_sink.events),
        "secret_leak_check": "passed",
    }
    assert_secret_free(report, secrets)
    return report


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a full local MCP smoke against the WEEX futures demo API.")
    parser.add_argument("--execute-demo-orders", action="store_true", help="Required acknowledgement that two demo MARKET orders will be submitted.")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--quantity", default="0.001")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if not args.execute_demo_orders:
        print("Refusing to run: pass --execute-demo-orders to authorize the demo open/cleanup pair.", file=sys.stderr)
        return 2
    api_key = getpass.getpass("WEEX demo API key: ")
    api_secret = getpass.getpass("WEEX demo secret key: ")
    api_passphrase = getpass.getpass("WEEX demo passphrase: ")
    secrets = (api_key, api_secret, api_passphrase)
    try:
        report = run_full_smoke(
            api_key=api_key,
            api_secret=api_secret,
            api_passphrase=api_passphrase,
            symbol=args.symbol,
            quantity=args.quantity,
        )
        assert_secret_free(report, secrets)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except BaseException as exc:
        failure = {"ok": False, "error_type": exc.__class__.__name__, "message": str(exc), "secret_leak_check": "passed"}
        try:
            assert_secret_free(failure, secrets)
        except RuntimeError:
            failure["message"] = "smoke failed; details suppressed because they contained credential material"
        print(json.dumps(failure, ensure_ascii=False, indent=2, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
