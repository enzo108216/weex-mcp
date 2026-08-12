from __future__ import annotations

from typing import Any

from ..adapters.rest_errors import WeexRestBusinessError
from ..adapters.trade_guard import TradeAdapterError, TradeGuardAdapter
from ..approval_token import ApprovalTokenCodec, ApprovalTokenError
from ..credential_context import CredentialContext, CredentialError
from ..response import error_response, ok_response
from ..secret_provider import ResolvedWeexCredential, SecretProviderError
from ..tenant_context import TenantContext
from ..trade_execution import CONFIRMATION_REPLY_TEXT, TradeExecution

_ORDER_REQUIRED_FIELDS = (
    "market",
    "trading_mode",
    "symbol",
    "side",
    "order_type",
    "quantity",
    "client_order_id",
)
_CANCEL_REQUIRED_FIELDS = ("market", "trading_mode")
_LEVERAGE_REQUIRED_FIELDS = ("market", "trading_mode", "symbol", "margin_mode", "leverage")
_MARGIN_MODE_REQUIRED_FIELDS = ("market", "trading_mode", "symbol", "margin_mode")
_GENERIC_MUTATION_ACTIONS = {
    "batch_order",
    "batch_cancel",
    "cancel_scope",
    "conditional_order",
    "conditional_cancel",
    "tp_sl",
    "close_positions",
    "position_margin",
    "auto_append_margin",
}
_GENERIC_MUTATION_REQUIRED_FIELDS = {
    "batch_order": ("market", "trading_mode", "orders"),
    "batch_cancel": ("market", "trading_mode"),
    "cancel_scope": ("market", "trading_mode", "order_kind", "scope"),
    "conditional_order": (
        "market",
        "trading_mode",
        "symbol",
        "side",
        "position_side",
        "conditional_type",
        "quantity",
        "trigger_price",
        "client_algo_id",
    ),
    "conditional_cancel": ("market", "trading_mode", "order_id"),
    "tp_sl": ("market", "trading_mode", "operation", "trigger_price"),
    "close_positions": ("market", "trading_mode", "scope"),
    "position_margin": (
        "market",
        "trading_mode",
        "isolated_position_id",
        "amount",
        "adjustment",
    ),
    "auto_append_margin": ("market", "trading_mode", "position_id", "enabled"),
}


def preview_order(
    arguments: dict[str, Any],
    *,
    context: TenantContext,
    trade_enabled: bool,
    approval_codec: ApprovalTokenCodec,
    adapter: TradeGuardAdapter,
) -> dict[str, Any]:
    return _preview_trade_action(
        arguments,
        context=context,
        trade_enabled=trade_enabled,
        approval_codec=approval_codec,
        adapter=adapter,
        action="order",
        required_fields=_ORDER_REQUIRED_FIELDS,
        preview_method="preview_order",
    )


def preview_cancel_order(
    arguments: dict[str, Any],
    *,
    context: TenantContext,
    trade_enabled: bool,
    approval_codec: ApprovalTokenCodec,
    adapter: TradeGuardAdapter,
) -> dict[str, Any]:
    if not arguments.get("order_id") and not arguments.get("client_order_id"):
        return error_response(
            "missing_fields",
            "Trade cancel preview requires order_id or client_order_id.",
            details={"fields": ["order_id", "client_order_id"]},
            audit=context.audit_dict(),
        )
    return _preview_trade_action(
        arguments,
        context=context,
        trade_enabled=trade_enabled,
        approval_codec=approval_codec,
        adapter=adapter,
        action="cancel_order",
        required_fields=_CANCEL_REQUIRED_FIELDS,
        preview_method="preview_cancel_order",
    )


def preview_leverage(
    arguments: dict[str, Any],
    *,
    context: TenantContext,
    trade_enabled: bool,
    approval_codec: ApprovalTokenCodec,
    adapter: TradeGuardAdapter,
) -> dict[str, Any]:
    return _preview_trade_action(
        arguments,
        context=context,
        trade_enabled=trade_enabled,
        approval_codec=approval_codec,
        adapter=adapter,
        action="update_leverage",
        required_fields=_LEVERAGE_REQUIRED_FIELDS,
        preview_method="preview_leverage",
    )


def preview_margin_mode(
    arguments: dict[str, Any],
    *,
    context: TenantContext,
    trade_enabled: bool,
    approval_codec: ApprovalTokenCodec,
    adapter: TradeGuardAdapter,
) -> dict[str, Any]:
    return _preview_trade_action(
        arguments,
        context=context,
        trade_enabled=trade_enabled,
        approval_codec=approval_codec,
        adapter=adapter,
        action="change_margin_mode",
        required_fields=_MARGIN_MODE_REQUIRED_FIELDS,
        preview_method="preview_margin_mode",
    )


def confirm_order(
    arguments: dict[str, Any],
    *,
    context: TenantContext,
    trade_enabled: bool,
    approval_codec: ApprovalTokenCodec,
    adapter: TradeGuardAdapter,
) -> dict[str, Any]:
    return _confirm_trade_action(
        arguments,
        context=context,
        trade_enabled=trade_enabled,
        approval_codec=approval_codec,
        adapter=adapter,
        action="order",
        required_fields=_ORDER_REQUIRED_FIELDS,
        preview_method="preview_order",
        confirm_method="confirm_order",
    )


def confirm_cancel_order(
    arguments: dict[str, Any],
    *,
    context: TenantContext,
    trade_enabled: bool,
    approval_codec: ApprovalTokenCodec,
    adapter: TradeGuardAdapter,
) -> dict[str, Any]:
    if not arguments.get("order_id") and not arguments.get("client_order_id"):
        return error_response(
            "missing_fields",
            "Trade cancel confirmation requires order_id or client_order_id.",
            details={"fields": ["order_id", "client_order_id"]},
            audit=context.audit_dict(),
        )
    return _confirm_trade_action(
        arguments,
        context=context,
        trade_enabled=trade_enabled,
        approval_codec=approval_codec,
        adapter=adapter,
        action="cancel_order",
        required_fields=_CANCEL_REQUIRED_FIELDS,
        preview_method="preview_cancel_order",
        confirm_method="confirm_cancel_order",
    )


def confirm_leverage(
    arguments: dict[str, Any],
    *,
    context: TenantContext,
    trade_enabled: bool,
    approval_codec: ApprovalTokenCodec,
    adapter: TradeGuardAdapter,
) -> dict[str, Any]:
    return _confirm_trade_action(
        arguments,
        context=context,
        trade_enabled=trade_enabled,
        approval_codec=approval_codec,
        adapter=adapter,
        action="update_leverage",
        required_fields=_LEVERAGE_REQUIRED_FIELDS,
        preview_method="preview_leverage",
        confirm_method="confirm_leverage",
    )


def confirm_margin_mode(
    arguments: dict[str, Any],
    *,
    context: TenantContext,
    trade_enabled: bool,
    approval_codec: ApprovalTokenCodec,
    adapter: TradeGuardAdapter,
) -> dict[str, Any]:
    return _confirm_trade_action(
        arguments,
        context=context,
        trade_enabled=trade_enabled,
        approval_codec=approval_codec,
        adapter=adapter,
        action="change_margin_mode",
        required_fields=_MARGIN_MODE_REQUIRED_FIELDS,
        preview_method="preview_margin_mode",
        confirm_method="confirm_margin_mode",
    )


def preview_mutation(
    arguments: dict[str, Any],
    *,
    action: str,
    context: TenantContext,
    trade_enabled: bool,
    approval_codec: ApprovalTokenCodec,
    adapter: TradeGuardAdapter,
) -> dict[str, Any]:
    if action not in _GENERIC_MUTATION_ACTIONS:
        return error_response("invalid_arguments", "Unsupported trade mutation action.", audit=context.audit_dict())
    return _preview_trade_action(
        arguments,
        context=context,
        trade_enabled=trade_enabled,
        approval_codec=approval_codec,
        adapter=adapter,
        action=action,
        required_fields=_GENERIC_MUTATION_REQUIRED_FIELDS[action],
        preview_method="preview_mutation",
    )


def confirm_mutation(
    arguments: dict[str, Any],
    *,
    action: str,
    context: TenantContext,
    trade_enabled: bool,
    approval_codec: ApprovalTokenCodec,
    adapter: TradeGuardAdapter,
) -> dict[str, Any]:
    if action not in _GENERIC_MUTATION_ACTIONS:
        return error_response("invalid_arguments", "Unsupported trade mutation action.", audit=context.audit_dict())
    return _confirm_trade_action(
        arguments,
        context=context,
        trade_enabled=trade_enabled,
        approval_codec=approval_codec,
        adapter=adapter,
        action=action,
        required_fields=_GENERIC_MUTATION_REQUIRED_FIELDS[action],
        preview_method="preview_mutation",
        confirm_method="confirm_mutation",
    )


def _preview_trade_action(
    arguments: dict[str, Any],
    *,
    context: TenantContext,
    trade_enabled: bool,
    approval_codec: ApprovalTokenCodec,
    adapter: TradeGuardAdapter,
    action: str,
    required_fields: tuple[str, ...],
    preview_method: str,
) -> dict[str, Any]:
    if not trade_enabled:
        return error_response("module_disabled", "Trade module is disabled.", audit=context.audit_dict())
    prepared = _prepare_execution(
        arguments,
        context=context,
        adapter=adapter,
        action=action,
        required_fields=required_fields,
        preview_method=preview_method,
    )
    if isinstance(prepared, dict):
        return prepared
    execution, resolved, credential = prepared
    try:
        token = approval_codec.issue(
            action=action,
            credential_fingerprint=resolved.fingerprint,
            summary=execution.summary,
            risk=execution.risk,
            risk_signature=execution.risk_signature,
        )
        claims = approval_codec.verify(token)
    except ApprovalTokenError as exc:
        return error_response(exc.code, exc.message, audit=context.audit_dict())
    return ok_response(
        request_id=context.request_id,
        preview_id=token,
        expires_at=claims["expires_at"],
        summary=execution.summary,
        risk=execution.risk,
        user_confirmation={
            "reply_text": CONFIRMATION_REPLY_TEXT,
            "reply_instruction": (
                "To accept the stated risks and continue, resubmit the complete original parameters, "
                "the preview_id, and the required confirmation object."
            ),
        },
        audit=context.audit_dict() | credential.safe_audit_dict(),
    )


def _confirm_trade_action(
    arguments: dict[str, Any],
    *,
    context: TenantContext,
    trade_enabled: bool,
    approval_codec: ApprovalTokenCodec,
    adapter: TradeGuardAdapter,
    action: str,
    required_fields: tuple[str, ...],
    preview_method: str,
    confirm_method: str,
) -> dict[str, Any]:
    if not trade_enabled:
        return error_response("module_disabled", "Trade module is disabled.", audit=context.audit_dict())
    preview_id = arguments.get("preview_id")
    legacy_approval_token = arguments.get("approval_token")
    if bool(preview_id) == bool(legacy_approval_token):
        return error_response(
            "invalid_arguments",
            "Trade confirmation requires exactly one of preview_id or approval_token.",
            audit=context.audit_dict(),
        )
    confirmation_error = _validate_confirmation(arguments.get("confirmation"))
    if confirmation_error is not None:
        return error_response("invalid_arguments", confirmation_error, audit=context.audit_dict())
    try:
        claims = approval_codec.verify(preview_id or legacy_approval_token)
    except ApprovalTokenError as exc:
        return error_response(exc.code, exc.message, audit=context.audit_dict())

    prepared = _prepare_execution(
        arguments,
        context=context,
        adapter=adapter,
        action=action,
        required_fields=required_fields,
        preview_method=preview_method,
    )
    if isinstance(prepared, dict):
        return prepared
    execution, resolved, credential = prepared
    audit = context.audit_dict() | credential.safe_audit_dict()
    if not _claims_match_execution(claims, execution=execution, credential_fingerprint=resolved.fingerprint):
        return error_response(
            "approval_mismatch",
            "Approval token does not match the confirmed trade request.",
            audit=audit,
        )
    try:
        result = getattr(adapter, confirm_method)(
            execution=execution,
            credential=resolved,
            credential_argument=dict(arguments["credential"]),
            context=context,
        )
    except SecretProviderError as exc:
        return error_response(exc.code, exc.safe_message(), audit=audit)
    except WeexRestBusinessError as exc:
        return error_response(
            "upstream_error",
            "WEEX trade API returned an error.",
            details=exc.safe_details(),
            audit=audit,
        )
    except ValueError as exc:
        return error_response("invalid_arguments", str(exc), audit=audit)
    except Exception as exc:
        return error_response(
            "upstream_error",
            "Trade adapter failed before returning a confirmed exchange result.",
            details={"reason": exc.__class__.__name__},
            audit=audit,
        )
    if not _has_confirm_success_evidence(result):
        return error_response("upstream_error", "WEEX did not provide explicit trade success evidence.", audit=audit)
    return ok_response(result=result, audit=audit)


def _prepare_execution(
    arguments: dict[str, Any],
    *,
    context: TenantContext,
    adapter: TradeGuardAdapter,
    action: str,
    required_fields: tuple[str, ...],
    preview_method: str,
) -> tuple[TradeExecution, ResolvedWeexCredential, CredentialContext] | dict[str, Any]:
    try:
        credential = CredentialContext.from_argument(arguments.get("credential"))
        credential.require_permission_if_declared("TRADE")
    except CredentialError as exc:
        return error_response(exc.code, exc.message, audit=context.audit_dict())
    audit = context.audit_dict() | credential.safe_audit_dict()
    missing = [field for field in required_fields if _is_missing(arguments, field)]
    if action == "order":
        if str(arguments.get("market") or "").strip().lower() in {"futures", "contract"} and not arguments.get("position_side"):
            missing.append("position_side")
        if str(arguments.get("order_type") or "").strip().upper() == "LIMIT":
            missing.extend(field for field in ("price", "time_in_force") if not arguments.get(field))
    if action == "update_leverage" and str(arguments.get("margin_mode") or "").strip().lower() == "isolated" and not arguments.get("position_side"):
        missing.append("position_side")
    if action == "batch_cancel" and not arguments.get("order_ids") and not arguments.get("client_order_ids"):
        missing.extend(("order_ids", "client_order_ids"))
    if action == "cancel_scope":
        scope = str(arguments.get("scope") or "").strip().lower()
        if scope == "symbol" and _is_missing(arguments, "symbol"):
            missing.append("symbol")
        if scope == "all" and arguments.get("acknowledge_all_symbols") is not True:
            missing.append("acknowledge_all_symbols")
    if action == "conditional_order" and str(arguments.get("conditional_type") or "").strip().upper() in {"STOP", "TAKE_PROFIT"} and _is_missing(arguments, "price"):
        missing.append("price")
    if action == "tp_sl":
        operation = str(arguments.get("operation") or "").strip().lower()
        if operation == "place":
            missing.extend(
                field
                for field in ("symbol", "client_algo_id", "plan_type", "position_side")
                if _is_missing(arguments, field)
            )
            if _is_missing(arguments, "quantity") and arguments.get("acknowledge_full_position") is not True:
                missing.append("quantity_or_acknowledge_full_position")
        elif operation == "modify" and _is_missing(arguments, "order_id"):
            missing.append("order_id")
    if action == "close_positions":
        scope = str(arguments.get("scope") or "").strip().lower()
        if scope == "position" and _is_missing(arguments, "position_id"):
            missing.append("position_id")
        if scope == "symbol" and _is_missing(arguments, "symbol"):
            missing.append("symbol")
        if scope == "all" and arguments.get("acknowledge_all_positions") is not True:
            missing.append("acknowledge_all_positions")
    if missing:
        return error_response(
            "missing_fields",
            "Trade request is missing required fields.",
            details={"fields": missing},
            audit=audit,
        )
    try:
        resolved = _resolve_adapter_credential(
            adapter,
            credential=credential,
            credential_argument=dict(arguments["credential"]),
            context=context,
        )
        preview_call = getattr(adapter, preview_method)
        preview_kwargs = {
            "arguments": arguments,
            "credential": resolved,
            "credential_argument": dict(arguments["credential"]),
            "context": context,
        }
        if preview_method == "preview_mutation":
            preview_kwargs["action"] = action
        preview_result = preview_call(**preview_kwargs)
        summary = _validated_preview_summary(preview_result, action=action, arguments=arguments)
        risk = _validated_preview_risk(preview_result)
        execution = TradeExecution(
            action=action,
            market=str(summary["market"]).strip().lower(),
            trading_mode=str(summary["trading_mode"]).strip().lower(),
            summary=summary,
            risk=risk,
            risk_signature=str(preview_result["risk_signature"]),
        )
        resolved = _validate_adapter_access(
            adapter,
            resolved=resolved,
            arguments=arguments,
            context=context,
        )
        return execution, resolved, credential
    except TradeAdapterError as exc:
        return error_response(exc.code, exc.message, audit=audit)
    except SecretProviderError as exc:
        return error_response(exc.code, exc.safe_message(), audit=audit)
    except WeexRestBusinessError as exc:
        return error_response(
            "upstream_error",
            "WEEX trade API returned an error.",
            details=exc.safe_details(),
            audit=audit,
        )
    except ValueError as exc:
        return error_response("invalid_arguments", str(exc), audit=audit)
    except Exception as exc:
        return error_response(
            "upstream_error",
            "Trade adapter failed before returning a preview result.",
            details={"reason": exc.__class__.__name__},
            audit=audit,
        )


def _is_missing(arguments: dict[str, Any], field: str) -> bool:
    return field not in arguments or arguments[field] is None or arguments[field] == ""


def _claims_match_execution(
    claims: dict[str, Any],
    *,
    execution: TradeExecution,
    credential_fingerprint: str,
) -> bool:
    return (
        claims.get("action") == execution.action
        and claims.get("credential_fingerprint") == credential_fingerprint
        and claims.get("summary") == execution.summary
        and claims.get("risk") == execution.risk
        and claims.get("risk_signature") == execution.risk_signature
    )


def _validate_confirmation(raw: Any) -> str | None:
    if not isinstance(raw, dict):
        return "confirmation must be an object."
    if raw.get("accepted") is not True:
        return "confirmation.accepted must be true."
    if str(raw.get("reply_text") or "").strip() != CONFIRMATION_REPLY_TEXT:
        return "confirmation.reply_text must match the required confirmation value."
    return None


def _resolve_adapter_credential(
    adapter: TradeGuardAdapter,
    *,
    credential: CredentialContext,
    credential_argument: dict[str, Any],
    context: TenantContext,
) -> ResolvedWeexCredential:
    resolver = getattr(adapter, "resolve_credential", None)
    if not callable(resolver):
        raise TradeAdapterError("adapter_not_configured", "Trade adapter must resolve request credentials.")
    try:
        resolved = resolver(credential_argument=credential_argument, context=context)
    except (TradeAdapterError, SecretProviderError, ValueError):
        raise
    except Exception as exc:
        raise TradeAdapterError(
            "credential_resolution_failed",
            "Trade adapter failed to resolve the request credential.",
        ) from exc
    if not isinstance(resolved, ResolvedWeexCredential) or not resolved.fingerprint:
        raise TradeAdapterError("credential_resolution_failed", "Trade adapter returned an invalid resolved credential.")
    return resolved


def _validate_adapter_access(
    adapter: TradeGuardAdapter,
    *,
    resolved: ResolvedWeexCredential,
    arguments: dict[str, Any],
    context: TenantContext,
) -> ResolvedWeexCredential:
    access_validator = getattr(adapter, "validate_trade_access", None)
    if callable(access_validator):
        resolved = access_validator(arguments=arguments, credential=resolved, context=context)
        if not isinstance(resolved, ResolvedWeexCredential) or not resolved.fingerprint:
            raise TradeAdapterError("credential_resolution_failed", "Trade adapter returned an invalid verified credential.")
    if "TRADE" not in resolved.permissions:
        raise TradeAdapterError("permission_denied", "Resolved credential does not allow TRADE.")
    return resolved


def _validated_preview_summary(preview_result: Any, *, action: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(preview_result, dict):
        raise WeexRestBusinessError(code="invalid_response", message="Trade adapter returned an invalid preview result.")
    risk_signature = preview_result.get("risk_signature")
    if not isinstance(risk_signature, str) or not risk_signature.strip():
        raise WeexRestBusinessError(code="invalid_response", message="Trade adapter preview did not contain a risk signature.")
    raw_summary = preview_result.get("summary")
    if not isinstance(raw_summary, dict):
        raise WeexRestBusinessError(code="invalid_response", message="Trade adapter preview did not contain a summary object.")
    summary = dict(raw_summary)
    expected_market = str(arguments.get("market") or "").strip().lower()
    expected_trading_mode = str(arguments.get("trading_mode") or "").strip().lower()
    if summary.get("action") != action:
        raise WeexRestBusinessError(code="invalid_response", message="Trade adapter preview action binding is invalid.")
    if str(summary.get("market") or "").strip().lower() != expected_market:
        raise WeexRestBusinessError(code="invalid_response", message="Trade adapter preview market binding is invalid.")
    if str(summary.get("trading_mode") or "").strip().lower() != expected_trading_mode:
        raise WeexRestBusinessError(code="invalid_response", message="Trade adapter preview trading mode binding is invalid.")
    if not isinstance(summary.get("endpoint"), str) or not summary["endpoint"].strip():
        raise WeexRestBusinessError(code="invalid_response", message="Trade adapter preview did not contain an execution endpoint.")
    query_actions = {"cancel_order", "cancel_scope", "conditional_cancel"}
    binding_field = "request_query" if action in query_actions else "request_body"
    binding = summary.get(binding_field)
    if not isinstance(binding, dict) or (not binding and action != "cancel_scope"):
        raise WeexRestBusinessError(
            code="invalid_response",
            message=f"Trade adapter preview did not contain the execution {binding_field}.",
        )
    return summary


def _validated_preview_risk(preview_result: dict[str, Any]) -> dict[str, Any]:
    raw_risk = preview_result.get("risk")
    if not isinstance(raw_risk, dict) or not raw_risk:
        raise WeexRestBusinessError(code="invalid_response", message="Trade adapter preview did not contain a risk object.")
    return dict(raw_risk)


def _has_confirm_success_evidence(result: Any) -> bool:
    return isinstance(result, dict) and result.get("success") is True
