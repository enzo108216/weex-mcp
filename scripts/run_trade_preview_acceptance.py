#!/usr/bin/env python3
"""Run Trade preview-only acceptance; never construct or call a confirm request."""

from __future__ import annotations

import getpass
import json
import os
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen


def read_service_token() -> str:
    for line in Path(".env").read_text().splitlines():
        if line.startswith("WEEX_SERVICE_AUTH_TOKEN="):
            return line.split("=", 1)[1]
    raise RuntimeError("WEEX_SERVICE_AUTH_TOKEN is missing")


def read_credentials() -> list[tuple[str, dict[str, str]]]:
    output = []
    for label in ("K1", "K2", "K3", "K4"):
        output.append(
            (
                label,
                {
                    "type": "plaintext",
                    "api_key": getpass.getpass(f"{label} api_key: "),
                    "api_secret": getpass.getpass(f"{label} api_secret: "),
                    "api_passphrase": getpass.getpass(f"{label} api_passphrase: "),
                },
            )
        )
    return output


def call_tool(token: str, name: str, arguments: dict[str, Any], request_id: int) -> dict[str, Any]:
    if not name.startswith("trade_preview_") and name != "account_get_positions":
        raise RuntimeError(f"unsafe tool blocked by preview acceptance driver: {name}")
    mcp_url = os.environ.get("WEEX_MCP_URL", "http://127.0.0.1:8080/mcp")
    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }
    request = Request(
        mcp_url,
        data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "MCP-Protocol-Version": "2025-06-18",
        },
        method="POST",
    )
    with urlopen(request, timeout=20) as response:
        return json.load(response)["result"]


def summarize(result: dict[str, Any]) -> dict[str, Any]:
    error = result.get("error") if isinstance(result.get("error"), dict) else {}
    risk = result.get("risk") if isinstance(result.get("risk"), dict) else {}
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    return {
        "ok": result.get("ok") is True,
        "preview_id_issued": isinstance(result.get("preview_id"), str) and bool(result["preview_id"]),
        "risk_status": risk.get("status"),
        "bound_action": summary.get("action"),
        "error_code": error.get("code"),
        "error_message": error.get("message"),
    }


def nonzero_position(item: dict[str, Any]) -> bool:
    for field in ("positionAmt", "quantity", "size", "availableQty", "holdVol"):
        if item.get(field) in (None, ""):
            continue
        try:
            return Decimal(str(item[field])) != 0
        except InvalidOperation:
            return False
    return False


def position_id(item: dict[str, Any] | None) -> str | None:
    if item is None:
        return None
    value = item.get("positionId") or item.get("isolatedPositionId") or item.get("id")
    return str(value) if value not in (None, "") else None


def is_isolated(item: dict[str, Any]) -> bool:
    return str(item.get("marginType") or item.get("marginMode") or "").upper() == "ISOLATED"


def load_position_context(token: str, credentials: list[tuple[str, dict[str, str]]]) -> dict[str, dict[str, str]]:
    contexts: dict[str, dict[str, str]] = {}
    request_id = 10
    for label, credential in credentials:
        request_id += 1
        result = call_tool(
            token,
            "account_get_positions",
            {"market": "futures", "trading_mode": "live", "credential": credential},
            request_id,
        )
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        positions = data.get("positions") if isinstance(data.get("positions"), list) else []
        open_positions = [item for item in positions if isinstance(item, dict) and nonzero_position(item)]
        any_position = next((item for item in open_positions if position_id(item)), None)
        isolated = next((item for item in open_positions if position_id(item) and is_isolated(item)), None)
        contexts[label] = {
            "open_position_id": position_id(any_position) or "",
            "open_position_symbol": str((any_position or {}).get("symbol") or ""),
            "isolated_position_id": position_id(isolated) or "",
        }
    return contexts


def cases(context: dict[str, str]) -> list[tuple[str, str, dict[str, Any]]]:
    client_id = "wwld15863preview001"
    base_order = {
        "market": "futures",
        "trading_mode": "live",
        "symbol": "BTCUSDT",
        "side": "BUY",
        "position_side": "LONG",
        "order_type": "MARKET",
        "quantity": "0.0001",
        "client_order_id": client_id,
    }
    batch_order = {key: value for key, value in base_order.items() if key not in {"market", "trading_mode"}}
    output = [
        ("order_futures", "trade_preview_order", base_order),
        ("order_futures_demo", "trade_preview_order", base_order | {"trading_mode": "demo", "client_order_id": client_id + "d"}),
        ("order_spot", "trade_preview_order", {"market": "spot", "trading_mode": "live", "symbol": "BTCUSDT", "side": "BUY", "order_type": "MARKET", "quantity": "0.0001", "client_order_id": client_id + "s"}),
        ("cancel_order_futures", "trade_preview_cancel_order", {"market": "futures", "trading_mode": "live", "order_id": "1"}),
        ("cancel_order_spot", "trade_preview_cancel_order", {"market": "spot", "trading_mode": "live", "order_id": "1"}),
        ("leverage", "trade_preview_leverage", {"market": "futures", "trading_mode": "live", "symbol": "BTCUSDT", "margin_mode": "cross", "leverage": "2"}),
        ("margin_mode", "trade_preview_margin_mode", {"market": "futures", "trading_mode": "live", "symbol": "BTCUSDT", "margin_mode": "cross"}),
        ("batch_order_futures", "trade_preview_batch_order", {"market": "futures", "trading_mode": "live", "orders": [batch_order | {"order_type": "LIMIT", "price": "100", "time_in_force": "GTC"}]}),
        ("batch_cancel_futures", "trade_preview_batch_cancel", {"market": "futures", "trading_mode": "live", "order_ids": ["1"]}),
        ("cancel_scope_futures", "trade_preview_cancel_scope", {"market": "futures", "trading_mode": "live", "order_kind": "normal", "scope": "symbol", "symbol": "BTCUSDT"}),
        ("conditional_order", "trade_preview_conditional_order", {"market": "futures", "trading_mode": "live", "symbol": "BTCUSDT", "side": "BUY", "position_side": "LONG", "conditional_type": "STOP_MARKET", "quantity": "0.0001", "trigger_price": "100", "client_algo_id": "wwld15863algo001"}),
        ("conditional_cancel", "trade_preview_conditional_cancel", {"market": "futures", "trading_mode": "live", "order_id": "1"}),
        ("tp_sl_modify", "trade_preview_tp_sl", {"market": "futures", "trading_mode": "live", "operation": "modify", "order_id": "1", "trigger_price": "100"}),
    ]
    if context.get("open_position_id"):
        close = {"market": "futures", "trading_mode": "live", "scope": "position", "position_id": context["open_position_id"]}
        if context.get("open_position_symbol"):
            close["symbol"] = context["open_position_symbol"]
        output.append(("close_positions", "trade_preview_close_positions", close))
    if context.get("isolated_position_id"):
        output.extend(
            [
                ("position_margin", "trade_preview_position_margin", {"market": "futures", "trading_mode": "live", "isolated_position_id": context["isolated_position_id"], "amount": "0.01", "adjustment": "increase"}),
                ("auto_append_margin", "trade_preview_auto_append_margin", {"market": "futures", "trading_mode": "live", "position_id": context["isolated_position_id"], "enabled": True}),
            ]
        )
    return output


def main() -> None:
    token = read_service_token()
    credentials = read_credentials()
    position_context = load_position_context(token, credentials)
    all_case_names = {
        "order_futures", "order_futures_demo", "order_spot", "cancel_order_futures", "cancel_order_spot",
        "leverage", "margin_mode", "batch_order_futures", "batch_cancel_futures", "cancel_scope_futures",
        "conditional_order", "conditional_cancel", "tp_sl_modify", "close_positions", "position_margin", "auto_append_margin",
    }
    output = []
    completed: set[str] = set()
    request_id = 100
    for label, credential in credentials:
        for case, tool, arguments in cases(position_context[label]):
            if case in completed:
                continue
            request_id += 1
            try:
                result = call_tool(token, tool, arguments | {"credential": credential}, request_id)
                attempt = {"credential": label} | summarize(result)
            except Exception as exc:
                attempt = {"credential": label, "ok": False, "transport_error": f"{type(exc).__name__}: {exc}"}
            output.append({"case": case, "tool": tool, "attempt": attempt})
            print(json.dumps(output[-1], ensure_ascii=False), flush=True)
            if attempt["ok"]:
                completed.add(case)
    missing = sorted(all_case_names - completed)
    Path("/tmp/wwld-15863-trade-preview.json").write_text(json.dumps(output, ensure_ascii=False, indent=2))
    print(json.dumps({"expected": len(all_case_names), "passed": len(completed), "missing": missing}, ensure_ascii=False))


if __name__ == "__main__":
    main()
