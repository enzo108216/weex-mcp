from __future__ import annotations

from typing import Any

from ..adapters.account import AccountAdapter
from ..adapters.rest_errors import WeexRestBusinessError
from ..credential_context import CredentialContext, CredentialError
from ..response import error_response, ok_response
from ..secret_provider import SecretProviderError
from ..tenant_context import TenantContext


def get_balance(arguments: dict[str, Any], context: TenantContext, *, adapter: AccountAdapter) -> dict[str, Any]:
    return _call_account_adapter(
        arguments,
        context,
        required_fields=("market", "trading_mode"),
        call=lambda credential_argument: adapter.get_balance(
            market=str(arguments["market"]),
            trading_mode=str(arguments["trading_mode"]),
            credential_argument=credential_argument,
            context=context,
        ),
    )


def get_positions(arguments: dict[str, Any], context: TenantContext, *, adapter: AccountAdapter) -> dict[str, Any]:
    return _call_account_adapter(
        arguments,
        context,
        required_fields=("market", "trading_mode"),
        call=lambda credential_argument: adapter.get_positions(
            market=str(arguments["market"]),
            trading_mode=str(arguments["trading_mode"]),
            credential_argument=credential_argument,
            context=context,
            symbol=arguments.get("symbol"),
        ),
    )


def get_orders(arguments: dict[str, Any], context: TenantContext, *, adapter: AccountAdapter) -> dict[str, Any]:
    return _call_account_adapter(
        arguments,
        context,
        required_fields=("market", "trading_mode", "status"),
        call=lambda credential_argument: adapter.get_orders(
            market=str(arguments["market"]),
            trading_mode=str(arguments["trading_mode"]),
            credential_argument=credential_argument,
            context=context,
            status=str(arguments["status"]),
            symbol=arguments.get("symbol"),
            order_id=arguments.get("order_id"),
            client_order_id=arguments.get("client_order_id"),
            start_time=arguments.get("start_time"),
            end_time=arguments.get("end_time"),
            limit=arguments.get("limit"),
            page=arguments.get("page"),
        ),
    )


def get_trades(arguments: dict[str, Any], context: TenantContext, *, adapter: AccountAdapter) -> dict[str, Any]:
    return _call_account_adapter(
        arguments,
        context,
        required_fields=("market", "trading_mode"),
        call=lambda credential_argument: adapter.get_trades(
            market=str(arguments["market"]),
            trading_mode=str(arguments["trading_mode"]),
            credential_argument=credential_argument,
            context=context,
            symbol=arguments.get("symbol"),
            order_id=arguments.get("order_id"),
            start_time=arguments.get("start_time"),
            end_time=arguments.get("end_time"),
            limit=arguments.get("limit"),
        ),
    )


def get_bills(arguments: dict[str, Any], context: TenantContext, *, adapter: AccountAdapter) -> dict[str, Any]:
    return _call_account_adapter(
        arguments,
        context,
        required_fields=("market", "trading_mode"),
        call=lambda credential_argument: adapter.get_bills(
            market=str(arguments["market"]),
            trading_mode=str(arguments["trading_mode"]),
            credential_argument=credential_argument,
            context=context,
            bill_type=arguments.get("bill_type"),
            coin_id=arguments.get("coin_id"),
            asset=arguments.get("asset"),
            biz_type=arguments.get("biz_type"),
            income_type=arguments.get("income_type"),
            symbol=arguments.get("symbol"),
            start_time=arguments.get("start_time"),
            end_time=arguments.get("end_time"),
            month=arguments.get("month"),
            limit=arguments.get("limit"),
            page=arguments.get("page"),
            next_key_id=arguments.get("next_key_id"),
            next_key_time=arguments.get("next_key_time"),
        ),
    )


def _call_account_adapter(
    arguments: dict[str, Any],
    context: TenantContext,
    *,
    required_fields: tuple[str, ...],
    call: Any,
) -> dict[str, Any]:
    try:
        credential = CredentialContext.from_argument(arguments.get("credential"))
        credential.require_permission_if_declared("USER_DATA")
    except CredentialError as exc:
        return error_response(exc.code, exc.message, audit=context.audit_dict())

    audit = context.audit_dict() | credential.safe_audit_dict()
    missing = [field for field in required_fields if not arguments.get(field)]
    if missing:
        return error_response("missing_fields", "Account query is missing required fields.", details={"fields": missing}, audit=audit)
    try:
        data = call(dict(arguments["credential"]))
    except SecretProviderError as exc:
        return error_response(exc.code, exc.safe_message(), audit=audit)
    except ValueError as exc:
        return error_response("invalid_arguments", str(exc), audit=audit)
    except WeexRestBusinessError as exc:
        return error_response("upstream_error", "WEEX account API returned an error.", details=exc.safe_details(), audit=audit)
    except Exception as exc:
        return error_response(
            "upstream_error",
            "Account adapter failed before returning a balance result.",
            details={"reason": exc.__class__.__name__},
            audit=audit,
        )
    return ok_response(data=data, audit=audit)
