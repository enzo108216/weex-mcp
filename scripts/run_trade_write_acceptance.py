#!/usr/bin/env python3
"""Run bounded real Trade confirm acceptance against the local MCP server."""

from __future__ import annotations

import getpass
import json
import os
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

MAX_NOTIONAL_USDT = Decimal("10")
CONFIRMATION = {"accepted": True, "reply_text": "确认"}
ALLOWED_TOOL_PREFIXES = ("market_get_", "account_get_", "trade_preview_", "trade_confirm_")
DISALLOWED_ACCOUNT_TOOLS = {"account_get_transfer_records"}
RESULT_PATH = Path("/tmp/wwld-15863-trade-write.json")
CREDENTIAL_ORDER = ("K2", "K1", "K3", "K4")
SYMBOL = "ETHUSDT"
QUANTITY = "0.0001"


@dataclass(frozen=True)
class Case:
    name: str
    preview_tool: str
    confirm_tool: str
    arguments: dict[str, Any]
    cleanup: str | None = None
    requires_live_position: bool = False
    requires_isolated_position: bool = False


def read_service_token() -> str:
    for line in Path(".env").read_text().splitlines():
        if line.startswith("WEEX_SERVICE_AUTH_TOKEN="):
            return line.split("=", 1)[1]
    raise RuntimeError("WEEX_SERVICE_AUTH_TOKEN is missing")


def read_credential(label: str) -> dict[str, str]:
    return {
        "type": "plaintext",
        "api_key": getpass.getpass(f"{label} api_key: "),
        "api_secret": getpass.getpass(f"{label} api_secret: "),
        "api_passphrase": getpass.getpass(f"{label} api_passphrase: "),
    }


def validate_tool_allowed(name: str) -> None:
    if not name.startswith(ALLOWED_TOOL_PREFIXES) or name in DISALLOWED_ACCOUNT_TOOLS:
        raise RuntimeError(f"unsafe tool blocked by write acceptance driver: {name}")


def call_tool(token: str, name: str, arguments: dict[str, Any], request_id: int) -> dict[str, Any]:
    validate_tool_allowed(name)
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
    with urlopen(request, timeout=30) as response:
        return json.load(response)["result"]


def confirm_arguments(arguments: dict[str, Any], preview_id: str) -> dict[str, Any]:
    return dict(arguments) | {"preview_id": preview_id, "confirmation": dict(CONFIRMATION)}


def decimal_value(value: Any, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{field} is not a valid decimal.") from None
    if not result.is_finite() or result <= 0:
        raise ValueError(f"{field} must be positive.")
    return result


def ensure_order_limit(order: dict[str, Any], prices: dict[str, Decimal]) -> None:
    symbol = str(order.get("symbol") or "").upper()
    quantity = decimal_value(order.get("quantity"), "quantity")
    order_type = str(order.get("order_type") or "").upper()
    if order_type == "LIMIT":
        price = decimal_value(order.get("price"), "price")
    else:
        price = prices.get(symbol)
        if price is None:
            raise ValueError(f"Cannot calculate notional for {symbol} market order.")
    notional = quantity * price
    if notional > MAX_NOTIONAL_USDT:
        raise ValueError(f"Order notional {notional} exceeds 10 USDT limit.")


def enforce_case_limit(arguments: dict[str, Any], prices: dict[str, Decimal]) -> None:
    if "orders" in arguments:
        for item in arguments["orders"]:
            ensure_order_limit(item, prices)
    elif "quantity" in arguments and "symbol" in arguments:
        ensure_order_limit(arguments, prices)


def summarize_result(result: dict[str, Any]) -> dict[str, Any]:
    error = result.get("error") if isinstance(result.get("error"), dict) else {}
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    raw_result = result.get("result") if isinstance(result.get("result"), dict) else {}
    output = {
        "ok": result.get("ok") is True,
        "preview_id_issued": isinstance(result.get("preview_id"), str) and bool(result.get("preview_id")),
        "error_code": error.get("code"),
        "error_message": error.get("message"),
        "error_details": error.get("details"),
    }
    exchange_result = raw_result or data.get("result") if isinstance(data, dict) else {}
    if isinstance(exchange_result, dict):
        for source, target in (
            ("exchange_order_id", "exchange_order_id"),
            ("client_order_id", "client_order_id"),
            ("status", "status"),
        ):
            if exchange_result.get(source) not in (None, ""):
                output[target] = exchange_result[source]
    return output


def extract_order_identity(result: dict[str, Any]) -> dict[str, str]:
    raw_result = result.get("result") if isinstance(result.get("result"), dict) else {}
    order_id = raw_result.get("exchange_order_id") or raw_result.get("order_id")
    client_id = raw_result.get("client_order_id")
    output: dict[str, str] = {}
    if order_id not in (None, ""):
        output["order_id"] = str(order_id)
    if client_id not in (None, ""):
        output["client_order_id"] = str(client_id)
    return output


def extract_batch_identities(result: dict[str, Any]) -> list[dict[str, str]]:
    raw_result = result.get("result") if isinstance(result.get("result"), dict) else {}
    items = raw_result.get("items") if isinstance(raw_result.get("items"), list) else []
    output = []
    for item in items:
        if not isinstance(item, dict) or item.get("ok") is not True:
            continue
        identity: dict[str, str] = {}
        if item.get("order_id") not in (None, ""):
            identity["order_id"] = str(item["order_id"])
        if item.get("client_order_id") not in (None, ""):
            identity["client_order_id"] = str(item["client_order_id"])
        if identity:
            output.append(identity)
    return output


def extract_first_item_order_id(result: dict[str, Any]) -> str | None:
    raw_result = result.get("result") if isinstance(result.get("result"), dict) else {}
    items = raw_result.get("items") if isinstance(raw_result.get("items"), list) else []
    for item in items:
        if isinstance(item, dict) and item.get("ok") is True and item.get("order_id") not in (None, ""):
            return str(item["order_id"])
    return None


def cancel_order_arguments(identity: dict[str, str]) -> dict[str, Any]:
    arguments: dict[str, Any] = {"market": "futures", "trading_mode": "live"}
    if identity.get("order_id"):
        arguments["order_id"] = identity["order_id"]
    elif identity.get("client_order_id"):
        arguments["client_order_id"] = identity["client_order_id"]
    return arguments


def get_price(token: str, symbol: str, request_id: int) -> Decimal:
    result = call_tool(
        token,
        "market_get_symbol_price",
        {"market": "futures", "symbol": symbol},
        request_id,
    )
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    price = data.get("price") or data.get("lastPrice") or data.get("last_price")
    if price in (None, "") and isinstance(data.get("raw"), dict):
        raw = data["raw"]
        price = raw.get("price") or raw.get("lastPrice") or raw.get("last")
    return decimal_value(price, f"{symbol} price")


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


def load_position_context(token: str, credential: dict[str, str], request_id: int) -> dict[str, str]:
    result = call_tool(
        token,
        "account_get_positions",
        {"market": "futures", "trading_mode": "live", "symbol": SYMBOL, "credential": credential},
        request_id,
    )
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    positions = data.get("positions") if isinstance(data.get("positions"), list) else []
    open_positions = [item for item in positions if isinstance(item, dict) and nonzero_position(item)]
    any_position = next((item for item in open_positions if position_id(item)), None)
    isolated = next((item for item in open_positions if position_id(item) and is_isolated(item)), None)
    return {
        "open_position_id": position_id(any_position) or "",
        "open_position_symbol": str((any_position or {}).get("symbol") or ""),
        "isolated_position_id": position_id(isolated) or "",
    }


def base_cases(run_id: str, price: Decimal) -> list[Case]:
    qty = QUANTITY
    order_client = f"wwld{run_id}o"
    batch_client = f"wwld{run_id}b"
    conditional_client = f"wwld{run_id}c"
    tp_sl_client = f"wwld{run_id}t"
    safe_limit = str((price * Decimal("0.5")).quantize(Decimal("0.01")))
    safe_trigger = str((price * Decimal("1.5")).quantize(Decimal("0.01")))
    limit_order = {
        "market": "futures",
        "trading_mode": "demo",
        "symbol": SYMBOL,
        "side": "BUY",
        "position_side": "LONG",
        "order_type": "LIMIT",
        "quantity": qty,
        "price": safe_limit,
        "time_in_force": "GTC",
        "client_order_id": order_client,
    }
    batch_order = {
        "symbol": SYMBOL,
        "side": "BUY",
        "position_side": "LONG",
        "order_type": "LIMIT",
        "quantity": qty,
        "price": safe_limit,
        "time_in_force": "GTC",
        "client_order_id": batch_client,
    }
    return [
        Case(
            "order_futures_live_limit",
            "trade_preview_order",
            "trade_confirm_order",
            limit_order | {"trading_mode": "live", "client_order_id": order_client + "l"},
            cleanup="cancel_order",
        ),
        Case(
            "leverage_futures_live",
            "trade_preview_leverage",
            "trade_confirm_leverage",
            {"market": "futures", "trading_mode": "live", "symbol": SYMBOL, "margin_mode": "cross", "leverage": "2"},
        ),
        Case(
            "margin_mode_futures_live",
            "trade_preview_margin_mode",
            "trade_confirm_margin_mode",
            {"market": "futures", "trading_mode": "live", "symbol": SYMBOL, "margin_mode": "cross"},
        ),
        Case(
            "batch_order_futures_live_limit",
            "trade_preview_batch_order",
            "trade_confirm_batch_order",
            {"market": "futures", "trading_mode": "live", "orders": [batch_order]},
            cleanup="batch_cancel",
        ),
        Case(
            "cancel_scope_futures_symbol",
            "trade_preview_cancel_scope",
            "trade_confirm_cancel_scope",
            {"market": "futures", "trading_mode": "live", "order_kind": "normal", "scope": "symbol", "symbol": SYMBOL},
        ),
        Case(
            "conditional_order_futures_live",
            "trade_preview_conditional_order",
            "trade_confirm_conditional_order",
            {
                "market": "futures",
                "trading_mode": "live",
                "symbol": SYMBOL,
                "side": "BUY",
                "position_side": "LONG",
                "conditional_type": "STOP_MARKET",
                "quantity": qty,
                "trigger_price": safe_trigger,
                "client_algo_id": conditional_client,
            },
        ),
        Case(
            "tp_sl_place_futures_live",
            "trade_preview_tp_sl",
            "trade_confirm_tp_sl",
            {
                "market": "futures",
                "trading_mode": "live",
                "operation": "place",
                "symbol": SYMBOL,
                "client_algo_id": tp_sl_client,
                "plan_type": "TAKE_PROFIT",
                "trigger_price": safe_trigger,
                "quantity": qty,
                "position_side": "LONG",
            },
            cleanup="conditional_scope",
        ),
        Case(
            "close_positions_futures_live",
            "trade_preview_close_positions",
            "trade_confirm_close_positions",
            {"market": "futures", "trading_mode": "live", "scope": "position"},
            requires_live_position=True,
        ),
        Case(
            "position_margin_futures_live",
            "trade_preview_position_margin",
            "trade_confirm_position_margin",
            {"market": "futures", "trading_mode": "live", "amount": "0.01", "adjustment": "increase"},
            requires_isolated_position=True,
        ),
        Case(
            "auto_append_margin_futures_live",
            "trade_preview_auto_append_margin",
            "trade_confirm_auto_append_margin",
            {"market": "futures", "trading_mode": "live", "enabled": True},
            requires_isolated_position=True,
        ),
    ]


def contextualize_case(case: Case, context: dict[str, str]) -> Case | None:
    arguments = dict(case.arguments)
    if case.requires_live_position:
        if not context.get("open_position_id"):
            return None
        arguments["position_id"] = context["open_position_id"]
        if context.get("open_position_symbol"):
            arguments["symbol"] = context["open_position_symbol"]
    if case.requires_isolated_position:
        if not context.get("isolated_position_id"):
            return None
        if "isolated_position_id" in arguments:
            arguments["isolated_position_id"] = context["isolated_position_id"]
        else:
            arguments["position_id"] = context["isolated_position_id"]
    return Case(
        name=case.name,
        preview_tool=case.preview_tool,
        confirm_tool=case.confirm_tool,
        arguments=arguments,
        cleanup=case.cleanup,
        requires_live_position=case.requires_live_position,
        requires_isolated_position=case.requires_isolated_position,
    )


def classify_failure(summary: dict[str, Any]) -> str:
    code = str(summary.get("error_code") or "")
    message = str(summary.get("error_message") or "")
    if code in {"permission_denied", "credential_resolution_failed", "missing_credentials"}:
        return "权限问题"
    if code in {"invalid_arguments", "missing_fields", "approval_mismatch", "approval_token_invalid"}:
        return "实现问题"
    if code in {"upstream_error"} or message:
        return "官方api问题"
    return "未分类"


def run_preview_confirm(
    *,
    token: str,
    case: Case,
    credential: dict[str, str],
    prices: dict[str, Decimal],
    request_id: int,
) -> tuple[dict[str, Any], int]:
    enforce_case_limit(case.arguments, prices)
    preview = call_tool(token, case.preview_tool, case.arguments | {"credential": credential}, request_id)
    request_id += 1
    preview_summary = summarize_result(preview)
    if not preview_summary["ok"] or not preview_summary["preview_id_issued"]:
        return {"preview": preview_summary, "confirm": None, "classification": classify_failure(preview_summary)}, request_id
    confirm = call_tool(
        token,
        case.confirm_tool,
        confirm_arguments(case.arguments | {"credential": credential}, str(preview["preview_id"])),
        request_id,
    )
    request_id += 1
    confirm_summary = summarize_result(confirm)
    return {
        "preview": preview_summary,
        "confirm": confirm_summary,
        "identity": extract_order_identity(confirm),
        "confirm_raw": confirm,
        "classification": None if confirm_summary["ok"] else classify_failure(confirm_summary),
    }, request_id


def make_case(name: str, action: str, arguments: dict[str, Any]) -> Case:
    return Case(name, f"trade_preview_{action}", f"trade_confirm_{action}", arguments)


def cleanup_for_result(
    *,
    token: str,
    source_case: Case,
    source_result: dict[str, Any],
    credential: dict[str, str],
    prices: dict[str, Decimal],
    request_id: int,
) -> tuple[list[dict[str, Any]], int]:
    cleanup_rows: list[dict[str, Any]] = []
    if source_case.cleanup == "cancel_order":
        identity = source_result.get("identity") if isinstance(source_result.get("identity"), dict) else {}
        if identity:
            arguments = cancel_order_arguments(identity)
            case = make_case("cancel_order_futures_live", "cancel_order", arguments)
            result, request_id = run_preview_confirm(
                token=token, case=case, credential=credential, prices=prices, request_id=request_id
            )
            cleanup_rows.append({"case": case.name, "result": result})
        case = make_case(
            "cancel_scope_futures_symbol",
            "cancel_scope",
            {"market": "futures", "trading_mode": "live", "order_kind": "normal", "scope": "symbol", "symbol": SYMBOL},
        )
        result, request_id = run_preview_confirm(
            token=token, case=case, credential=credential, prices=prices, request_id=request_id
        )
        cleanup_rows.append({"case": case.name, "result": result})
    elif source_case.cleanup == "batch_cancel":
        confirm_raw = source_result.get("confirm_raw") if isinstance(source_result.get("confirm_raw"), dict) else {}
        identities = extract_batch_identities(confirm_raw)
        order_ids = [identity["order_id"] for identity in identities if identity.get("order_id")]
        client_ids = [identity["client_order_id"] for identity in identities if identity.get("client_order_id")]
        if order_ids:
            selectors: dict[str, Any] = {"order_ids": order_ids}
        elif client_ids:
            selectors = {"client_order_ids": client_ids}
        else:
            return [{"case": "batch_cancel_futures_live", "error": "batch order returned no identity"}], request_id
        case = make_case(
            "batch_cancel_futures_live",
            "batch_cancel",
            {"market": "futures", "trading_mode": "live"} | selectors,
        )
        result, request_id = run_preview_confirm(
            token=token, case=case, credential=credential, prices=prices, request_id=request_id
        )
        cleanup_rows.append({"case": case.name, "result": result})
    elif source_case.name == "conditional_order_futures_live":
        identity = source_result.get("identity") if isinstance(source_result.get("identity"), dict) else {}
        if not identity.get("order_id"):
            return [{"case": "conditional_cancel_futures_live", "error": "conditional order returned no order_id"}], request_id
        case = make_case(
            "conditional_cancel_futures_live",
            "conditional_cancel",
            {"market": "futures", "trading_mode": "live", "order_id": identity["order_id"]},
        )
        result, request_id = run_preview_confirm(
            token=token, case=case, credential=credential, prices=prices, request_id=request_id
        )
        cleanup_rows.append({"case": case.name, "result": result})
    elif source_case.cleanup == "conditional_scope":
        case = make_case(
            "cancel_scope_conditional_futures_symbol",
            "cancel_scope",
            {"market": "futures", "trading_mode": "live", "order_kind": "conditional", "scope": "symbol", "symbol": SYMBOL},
        )
        result, request_id = run_preview_confirm(
            token=token, case=case, credential=credential, prices=prices, request_id=request_id
        )
        cleanup_rows.append({"case": case.name, "result": result})
    return cleanup_rows, request_id


def main() -> None:
    token = read_service_token()
    run_id = str(int(time.time()))[-10:]
    price = get_price(token, SYMBOL, 1)
    prices = {SYMBOL: price}
    completed: set[str] = set()
    output: list[dict[str, Any]] = []
    request_id = 100
    expected_cases = {case.name for case in base_cases(run_id, price)}
    expected_cases.update({
        "cancel_order_futures_live",
        "batch_cancel_futures_live",
        "conditional_cancel_futures_live",
        "cancel_scope_conditional_futures_symbol",
    })
    for label in CREDENTIAL_ORDER:
        if expected_cases <= completed:
            break
        credential = read_credential(label)
        request_id += 1
        context = load_position_context(token, credential, request_id)
        for raw_case in base_cases(run_id, price):
            if raw_case.name in completed:
                continue
            case = contextualize_case(raw_case, context)
            if case is None:
                output.append({"case": raw_case.name, "credential": label, "skipped": "missing required live/isolated position"})
                print(json.dumps(output[-1], ensure_ascii=False), flush=True)
                continue
            try:
                result, request_id = run_preview_confirm(
                    token=token,
                    case=case,
                    credential=credential,
                    prices=prices,
                    request_id=request_id + 1,
                )
                row = {"case": case.name, "credential": label, "result": result}
                cleanup_rows, request_id = cleanup_for_result(
                    token=token,
                    source_case=case,
                    source_result=result,
                    credential=credential,
                    prices=prices,
                    request_id=request_id + 1,
                )
                if cleanup_rows:
                    row["cleanup"] = cleanup_rows
            except Exception as exc:
                row = {
                    "case": case.name,
                    "credential": label,
                    "result": {
                        "exception": f"{type(exc).__name__}: {exc}",
                        "classification": "实现问题" if isinstance(exc, ValueError) else "官方api问题",
                    },
                }
            output.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
            confirm = row.get("result", {}).get("confirm") if isinstance(row.get("result"), dict) else None
            if isinstance(confirm, dict) and confirm.get("ok") is True:
                completed.add(raw_case.name)
            for cleanup in row.get("cleanup", []):
                cleanup_result = cleanup.get("result") if isinstance(cleanup, dict) else None
                cleanup_confirm = cleanup_result.get("confirm") if isinstance(cleanup_result, dict) else None
                if isinstance(cleanup_confirm, dict) and cleanup_confirm.get("ok") is True:
                    completed.add(str(cleanup["case"]))
    RESULT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2))
    print(json.dumps({"total_cases": len(expected_cases), "passed": len(completed), "missing": sorted(expected_cases - completed), "result_path": str(RESULT_PATH)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
