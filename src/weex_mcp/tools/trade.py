from __future__ import annotations

from typing import Any

from ..adapters.rest_errors import WeexRestBusinessError
from ..adapters.trade_guard import (
    TradeAdapterError,
    TradeGuardAdapter,
    TradeMutationError,
    mutation_outcome,
    safe_outcome_identity,
)
from ..approval_token import ApprovalTokenCodec, ApprovalTokenError
from ..credential_context import CredentialContext, CredentialError
from ..execution_authorization import (
    ExecutionAuthorizationError,
    ExecutionAuthorizer,
    VerifiedExecutionAuthorization,
)
from ..response import error_response, ok_response
from ..secret_provider import ResolvedWeexCredential, SecretProviderError
from ..tenant_context import TenantContext
from ..trade_execution import TradeExecution

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
_LEVERAGE_BASE_REQUIRED_FIELDS = ("market", "trading_mode", "symbol")
_EXPLICIT_LEVERAGE_FIELDS = (
    "cross_leverage",
    "isolated_long_leverage",
    "isolated_short_leverage",
)
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
_CONFIRM_TOOL_ACTIONS = {
    "trade_confirm_order": "order",
    "trade_confirm_cancel_order": "cancel_order",
    "trade_confirm_leverage": "update_leverage",
    "trade_confirm_margin_mode": "change_margin_mode",
    **{f"trade_confirm_{action}": action for action in _GENERIC_MUTATION_ACTIONS},
}


def confirm_action_for_tool(name: str) -> str | None:
    return _CONFIRM_TOOL_ACTIONS.get(name)


def not_sent_outcome_for_confirm(
    name: str,
    arguments: dict[str, Any],
    *,
    include_items: bool,
    error_code: str | None = None,
) -> dict[str, Any] | None:
    action = confirm_action_for_tool(name)
    if action is None:
        return None
    return _confirm_outcome(
        action=action,
        arguments=arguments,
        outcome_class="not_sent",
        write_attempted=False,
        error_code=error_code,
        include_items=include_items,
    )


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
        return _with_clarification(
            error_response(
                "missing_fields",
                "Trade cancel preview requires order_id or client_order_id.",
                details={"fields": ["order_id", "client_order_id"]},
                audit=context.audit_dict(),
            )
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
        required_fields=_leverage_required_fields(arguments),
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
    execution_authorizer: ExecutionAuthorizer,
) -> dict[str, Any]:
    result = _confirm_trade_action(
        arguments,
        context=context,
        trade_enabled=trade_enabled,
        approval_codec=approval_codec,
        adapter=adapter,
        execution_authorizer=execution_authorizer,
        action="order",
        required_fields=_ORDER_REQUIRED_FIELDS,
        preview_method="preview_order",
        confirm_method="confirm_order",
    )
    return _ensure_prewrite_outcome(result, action="order", arguments=arguments)


def confirm_cancel_order(
    arguments: dict[str, Any],
    *,
    context: TenantContext,
    trade_enabled: bool,
    approval_codec: ApprovalTokenCodec,
    adapter: TradeGuardAdapter,
    execution_authorizer: ExecutionAuthorizer,
) -> dict[str, Any]:
    if not arguments.get("order_id") and not arguments.get("client_order_id"):
        result = error_response(
            "missing_fields",
            "Trade cancel confirmation requires order_id or client_order_id.",
            details={"fields": ["order_id", "client_order_id"]},
            audit=context.audit_dict(),
        )
        return _ensure_prewrite_outcome(result, action="cancel_order", arguments=arguments)
    result = _confirm_trade_action(
        arguments,
        context=context,
        trade_enabled=trade_enabled,
        approval_codec=approval_codec,
        adapter=adapter,
        execution_authorizer=execution_authorizer,
        action="cancel_order",
        required_fields=_CANCEL_REQUIRED_FIELDS,
        preview_method="preview_cancel_order",
        confirm_method="confirm_cancel_order",
    )
    return _ensure_prewrite_outcome(result, action="cancel_order", arguments=arguments)


def confirm_leverage(
    arguments: dict[str, Any],
    *,
    context: TenantContext,
    trade_enabled: bool,
    approval_codec: ApprovalTokenCodec,
    adapter: TradeGuardAdapter,
    execution_authorizer: ExecutionAuthorizer,
) -> dict[str, Any]:
    result = _confirm_trade_action(
        arguments,
        context=context,
        trade_enabled=trade_enabled,
        approval_codec=approval_codec,
        adapter=adapter,
        execution_authorizer=execution_authorizer,
        action="update_leverage",
        required_fields=_leverage_required_fields(arguments),
        preview_method="preview_leverage",
        confirm_method="confirm_leverage",
    )
    return _ensure_prewrite_outcome(result, action="update_leverage", arguments=arguments)


def confirm_margin_mode(
    arguments: dict[str, Any],
    *,
    context: TenantContext,
    trade_enabled: bool,
    approval_codec: ApprovalTokenCodec,
    adapter: TradeGuardAdapter,
    execution_authorizer: ExecutionAuthorizer,
) -> dict[str, Any]:
    result = _confirm_trade_action(
        arguments,
        context=context,
        trade_enabled=trade_enabled,
        approval_codec=approval_codec,
        adapter=adapter,
        execution_authorizer=execution_authorizer,
        action="change_margin_mode",
        required_fields=_MARGIN_MODE_REQUIRED_FIELDS,
        preview_method="preview_margin_mode",
        confirm_method="confirm_margin_mode",
    )
    return _ensure_prewrite_outcome(result, action="change_margin_mode", arguments=arguments)


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
    execution_authorizer: ExecutionAuthorizer,
) -> dict[str, Any]:
    if action not in _GENERIC_MUTATION_ACTIONS:
        result = error_response("invalid_arguments", "Unsupported trade mutation action.", audit=context.audit_dict())
        return _ensure_prewrite_outcome(result, action=action, arguments=arguments)
    result = _confirm_trade_action(
        arguments,
        context=context,
        trade_enabled=trade_enabled,
        approval_codec=approval_codec,
        adapter=adapter,
        execution_authorizer=execution_authorizer,
        action=action,
        required_fields=_GENERIC_MUTATION_REQUIRED_FIELDS[action],
        preview_method="preview_mutation",
        confirm_method="confirm_mutation",
    )
    return _ensure_prewrite_outcome(result, action=action, arguments=arguments)


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
        return _with_clarification(prepared)
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
            "accepted": True,
            "reply_instruction": (
                "To accept the stated risks and continue, resubmit the complete original parameters, "
                "the preview_id, and confirmation.accepted=true."
            ),
        },
        audit=context.audit_dict() | credential.safe_audit_dict(),
    )


def _ensure_prewrite_outcome(
    result: dict[str, Any],
    *,
    action: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    if "outcome" in result:
        return result
    error = result.get("error")
    error_code = error.get("code") if isinstance(error, dict) else None
    enriched = dict(result)
    enriched["outcome"] = _confirm_outcome(
        action=action,
        arguments=arguments,
        outcome_class="not_sent",
        write_attempted=False,
        error_code=error_code if isinstance(error_code, str) else None,
        include_items=True,
    )
    return enriched


def _confirm_outcome(
    *,
    action: str,
    arguments: dict[str, Any],
    outcome_class: str,
    write_attempted: bool,
    error_code: str | None = None,
    include_items: bool,
) -> dict[str, Any]:
    items = (
        _batch_outcome_items(
            action=action,
            arguments=arguments,
            outcome_class=outcome_class,
            write_attempted=write_attempted,
            error_code=error_code,
        )
        if include_items
        else None
    )
    return mutation_outcome(
        outcome_class=outcome_class,
        write_attempted=write_attempted,
        action=action,
        items=items,
    )


def _batch_outcome_items(
    *,
    action: str,
    arguments: dict[str, Any],
    outcome_class: str,
    write_attempted: bool,
    error_code: str | None,
) -> list[dict[str, Any]] | None:
    identity_field: str
    identities: Any
    if action == "batch_order":
        orders = arguments.get("orders")
        if not isinstance(orders, list) or not orders or not all(isinstance(order, dict) for order in orders):
            return None
        identities = [order.get("client_order_id") for order in orders]
        identity_field = "client_order_id"
    elif action == "batch_cancel":
        order_ids = arguments.get("order_ids")
        client_order_ids = arguments.get("client_order_ids")
        if isinstance(order_ids, list) and order_ids and client_order_ids in (None, []):
            identities = order_ids
            identity_field = "operation_reference"
        elif isinstance(client_order_ids, list) and client_order_ids and order_ids in (None, []):
            identities = client_order_ids
            identity_field = "client_order_id"
        else:
            return None
    else:
        return None
    normalized = [_safe_identity(identity) for identity in identities]
    if any(identity is None for identity in normalized):
        return None
    concrete = [identity for identity in normalized if identity is not None]
    if len(set(concrete)) != len(concrete):
        return None
    items: list[dict[str, Any]] = []
    for identity in concrete:
        item: dict[str, Any] = {
            "class": outcome_class,
            "write_attempted": write_attempted,
            "action": action,
            identity_field: identity,
        }
        if error_code:
            item["error_code"] = error_code
        items.append(item)
    return items


def _confirm_trade_action(
    arguments: dict[str, Any],
    *,
    context: TenantContext,
    trade_enabled: bool,
    approval_codec: ApprovalTokenCodec,
    adapter: TradeGuardAdapter,
    execution_authorizer: ExecutionAuthorizer,
    action: str,
    required_fields: tuple[str, ...],
    preview_method: str,
    confirm_method: str,
) -> dict[str, Any]:
    if not trade_enabled:
        return error_response("module_disabled", "Trade module is disabled.", audit=context.audit_dict())
    has_confirmation = "confirmation" in arguments
    has_delegated_authorization = "delegated_authorization" in arguments
    if has_confirmation == has_delegated_authorization:
        return error_response(
            "invalid_arguments",
            "Trade confirmation requires exactly one of confirmation or delegated_authorization.",
            audit=context.audit_dict(),
        )
    preview_id = arguments.get("preview_id")
    legacy_approval_token = arguments.get("approval_token")
    if bool(preview_id) == bool(legacy_approval_token):
        return error_response(
            "invalid_arguments",
            "Trade confirmation requires exactly one of preview_id or approval_token.",
            audit=context.audit_dict(),
        )
    if has_confirmation:
        confirmation_error = _validate_confirmation(arguments.get("confirmation"))
        if confirmation_error is not None:
            return error_response("invalid_arguments", confirmation_error, audit=context.audit_dict())
    else:
        delegated_ref = arguments.get("delegated_authorization")
        if (
            not isinstance(delegated_ref, dict)
            or set(delegated_ref) != {"ref"}
            or not isinstance(delegated_ref.get("ref"), str)
            or not delegated_ref["ref"]
        ):
            return error_response(
                "invalid_arguments",
                "delegated_authorization must contain exactly one non-empty ref.",
                audit=context.audit_dict(),
            )
    try:
        claims = approval_codec.verify(preview_id or legacy_approval_token)
    except ApprovalTokenError as exc:
        return error_response(exc.code, exc.message, audit=context.audit_dict())

    authorization: VerifiedExecutionAuthorization | None = None
    if has_delegated_authorization:
        delegated_credential = _resolve_delegated_credential(
            arguments,
            context=context,
            adapter=adapter,
        )
        if isinstance(delegated_credential, dict):
            return delegated_credential
        resolved_for_authorization, credential_for_authorization = delegated_credential
        authorization_audit = context.audit_dict() | credential_for_authorization.safe_audit_dict()
        signed_execution = _execution_from_signed_claims(
            claims,
            action=action,
            credential_fingerprint=resolved_for_authorization.fingerprint,
            audit=authorization_audit,
        )
        if isinstance(signed_execution, dict):
            return signed_execution
        authorization_result = _authorize_delegated_execution(
            authorizer=execution_authorizer,
            context=context,
            credential_fingerprint=resolved_for_authorization.fingerprint,
            execution=signed_execution,
            delegated_ref=dict(arguments["delegated_authorization"]),
            audit=authorization_audit,
        )
        if isinstance(authorization_result, dict):
            return authorization_result
        authorization = authorization_result

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
    if authorization is not None:
        audit.update(authorization.safe_audit_dict())
    try:
        result = getattr(adapter, confirm_method)(
            execution=execution,
            credential=resolved,
            credential_argument=dict(arguments["credential"]),
            context=context,
        )
    except TradeMutationError as exc:
        outcome = exc.outcome(action=action)
        if action in {"batch_order", "batch_cancel"} and "items" not in outcome:
            outcome = _confirm_outcome(
                action=action,
                arguments=arguments,
                outcome_class=outcome["class"],
                write_attempted=outcome["write_attempted"],
                error_code=_safe_identity(exc.details.get("upstream_code") or exc.code),
                include_items=True,
            )
        return error_response(
            exc.code,
            exc.message,
            details=exc.details or None,
            audit=audit,
            outcome=outcome,
        )
    except SecretProviderError as exc:
        return error_response(
            exc.code,
            exc.safe_message(),
            audit=audit,
            outcome=_confirm_outcome(
                action=action,
                arguments=arguments,
                outcome_class="unknown",
                write_attempted=True,
                error_code=exc.code,
                include_items=True,
            ),
        )
    except WeexRestBusinessError as exc:
        return error_response(
            "upstream_error",
            "WEEX trade API returned an error.",
            details=exc.safe_details(),
            audit=audit,
            outcome=_confirm_outcome(
                action=action,
                arguments=arguments,
                outcome_class="unknown",
                write_attempted=True,
                error_code=exc.safe_details().get("upstream_code"),
                include_items=True,
            ),
        )
    except ValueError as exc:
        return error_response(
            "upstream_error",
            "Trade adapter failed after the mutation result became uncertain.",
            details={"reason": exc.__class__.__name__},
            audit=audit,
            outcome=_confirm_outcome(
                action=action,
                arguments=arguments,
                outcome_class="unknown",
                write_attempted=True,
                error_code="upstream_error",
                include_items=True,
            ),
        )
    except Exception as exc:
        return error_response(
            "upstream_error",
            "Trade adapter failed after the mutation result became uncertain.",
            details={"reason": exc.__class__.__name__},
            audit=audit,
            outcome=_confirm_outcome(
                action=action,
                arguments=arguments,
                outcome_class="unknown",
                write_attempted=True,
                error_code="upstream_error",
                include_items=True,
            ),
        )
    if not _has_confirm_success_evidence(result):
        return error_response(
            "upstream_error",
            "WEEX did not provide explicit trade success evidence.",
            audit=audit,
            outcome=_confirm_outcome(
                action=action,
                arguments=arguments,
                outcome_class="unknown",
                write_attempted=True,
                error_code="upstream_error",
                include_items=True,
            ),
        )
    public_result = dict(result)
    adapter_outcome = public_result.pop("_outcome", None)
    outcome = _confirmed_result_outcome(action=action, execution=execution, result=public_result, adapter_outcome=adapter_outcome)
    return ok_response(result=public_result, audit=audit, outcome=outcome)


def _confirmed_result_outcome(
    *,
    action: str,
    execution: TradeExecution,
    result: dict[str, Any],
    adapter_outcome: Any,
) -> dict[str, Any]:
    is_batch = action in {"batch_order", "batch_cancel"}
    if isinstance(adapter_outcome, dict):
        required = {"class", "write_attempted", "action"}
        if required <= set(adapter_outcome):
            if adapter_outcome.get("action") != action:
                return mutation_outcome(outcome_class="unknown", write_attempted=True, action=action)
            adapter_class = adapter_outcome.get("class")
            if (
                is_batch
                and adapter_class in {"accepted", "partial", "definitive_rejection"}
                and not isinstance(adapter_outcome.get("items"), list)
            ):
                return mutation_outcome(outcome_class="unknown", write_attempted=True, action=action)
            try:
                projected = mutation_outcome(
                    outcome_class=str(adapter_outcome["class"]),
                    write_attempted=adapter_outcome["write_attempted"],
                    action=action,
                    client_order_id=_safe_identity(adapter_outcome.get("client_order_id")),
                    operation_reference=_safe_identity(adapter_outcome.get("operation_reference")),
                    reconcile_identity=_safe_identity(adapter_outcome.get("reconcile_identity")),
                    items=adapter_outcome.get("items") if isinstance(adapter_outcome.get("items"), list) else None,
                )
                if is_batch and projected["class"] != "unknown" and "items" not in projected:
                    return mutation_outcome(outcome_class="unknown", write_attempted=True, action=action)
                return projected
            except ValueError:
                return mutation_outcome(outcome_class="unknown", write_attempted=True, action=action)
    items = result.get("items")
    if isinstance(items, list):
        item_outcomes = _confirmed_item_outcomes(action=action, items=items)
        if item_outcomes is None:
            return mutation_outcome(outcome_class="unknown", write_attempted=True, action=action)
        classes = {item["class"] for item in item_outcomes}
        if classes == {"accepted"}:
            outcome_class = "accepted"
        elif classes == {"definitive_rejection"}:
            outcome_class = "definitive_rejection"
        elif classes <= {"accepted", "definitive_rejection"}:
            outcome_class = "partial"
        else:
            outcome_class = "unknown"
        return mutation_outcome(
            outcome_class=outcome_class,
            write_attempted=True,
            action=action,
            items=item_outcomes,
        )
    if is_batch:
        return mutation_outcome(outcome_class="unknown", write_attempted=True, action=action)
    client_order_id = _safe_identity(result.get("client_order_id"))
    if client_order_id is None and action in {"order", "conditional_order"}:
        client_order_id = _safe_identity(
            (execution.summary.get("request_body") or {}).get("newClientOrderId")
            if isinstance(execution.summary.get("request_body"), dict)
            else None
        )
    operation_reference = _safe_identity(
        result.get("operation_reference")
        or result.get("exchange_order_id")
        or result.get("order_id")
        or result.get("position_id")
    )
    return mutation_outcome(
        outcome_class="accepted",
        write_attempted=True,
        action=action,
        client_order_id=client_order_id,
        operation_reference=operation_reference,
        reconcile_identity=_safe_identity(result.get("reconcile_identity")),
    )


def _confirmed_item_outcomes(*, action: str, items: list[Any]) -> list[dict[str, Any]] | None:
    if not items:
        return None
    outcomes: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            return None
        client_order_id = _safe_identity(item.get("client_order_id"))
        operation_reference = _safe_identity(
            item.get("operation_reference")
            or item.get("order_id")
            or item.get("position_id")
            or item.get("requested_order_id")
        )
        if client_order_id is None and operation_reference is None:
            return None
        if item.get("ok") is True:
            item_outcome = mutation_outcome(
                outcome_class="accepted",
                write_attempted=True,
                action=action,
                client_order_id=client_order_id,
                operation_reference=operation_reference,
            )
        elif item.get("ok") is False and isinstance(item.get("error"), dict):
            error_code = _safe_identity(item["error"].get("code"))
            if error_code is None:
                return None
            item_outcome = mutation_outcome(
                outcome_class="definitive_rejection",
                write_attempted=True,
                action=action,
                client_order_id=client_order_id,
                operation_reference=operation_reference,
            )
            item_outcome["error_code"] = error_code
        else:
            return None
        outcomes.append(item_outcome)
    return outcomes


def _safe_identity(value: Any) -> str | None:
    return safe_outcome_identity(value)


def _resolve_delegated_credential(
    arguments: dict[str, Any],
    *,
    context: TenantContext,
    adapter: TradeGuardAdapter,
) -> tuple[ResolvedWeexCredential, CredentialContext] | dict[str, Any]:
    try:
        credential = CredentialContext.from_argument(arguments.get("credential"))
        credential.require_permission_if_declared("TRADE")
    except CredentialError as exc:
        return error_response(exc.code, exc.message, audit=context.audit_dict())
    audit = context.audit_dict() | credential.safe_audit_dict()
    try:
        resolved = _resolve_adapter_credential(
            adapter,
            credential=credential,
            credential_argument=dict(arguments["credential"]),
            context=context,
        )
    except TradeAdapterError as exc:
        return error_response(exc.code, exc.message, audit=audit)
    except SecretProviderError as exc:
        return error_response(exc.code, exc.safe_message(), audit=audit)
    except ValueError as exc:
        return error_response("invalid_arguments", str(exc), audit=audit)
    except Exception as exc:
        return error_response(
            "credential_resolution_failed",
            "Trade adapter failed to resolve the request credential.",
            details={"reason": exc.__class__.__name__},
            audit=audit,
        )
    return resolved, credential


def _execution_from_signed_claims(
    claims: dict[str, Any],
    *,
    action: str,
    credential_fingerprint: str,
    audit: dict[str, Any],
) -> TradeExecution | dict[str, Any]:
    if claims.get("action") != action or claims.get("credential_fingerprint") != credential_fingerprint:
        return error_response(
            "approval_mismatch",
            "Approval token does not match the confirmed trade request.",
            audit=audit,
        )
    summary = claims.get("summary")
    risk = claims.get("risk")
    risk_signature = claims.get("risk_signature")
    if (
        not isinstance(summary, dict)
        or summary.get("action") != action
        or not isinstance(summary.get("market"), str)
        or not summary["market"].strip()
        or not isinstance(summary.get("trading_mode"), str)
        or not summary["trading_mode"].strip()
        or not isinstance(summary.get("endpoint"), str)
        or not summary["endpoint"].strip()
        or not isinstance(risk, dict)
        or not risk
        or not isinstance(risk_signature, str)
        or not risk_signature.strip()
    ):
        return error_response(
            "approval_token_invalid",
            "Approval token is invalid.",
            audit=audit,
        )
    binding_field = "request_query" if action in {"cancel_order", "cancel_scope", "conditional_cancel"} else "request_body"
    binding = summary.get(binding_field)
    if not isinstance(binding, dict) or (not binding and action != "cancel_scope"):
        return error_response(
            "approval_token_invalid",
            "Approval token is invalid.",
            audit=audit,
        )
    return TradeExecution(
        action=action,
        market=summary["market"].strip().lower(),
        trading_mode=summary["trading_mode"].strip().lower(),
        summary=dict(summary),
        risk=dict(risk),
        risk_signature=risk_signature,
    )


def _authorize_delegated_execution(
    *,
    authorizer: ExecutionAuthorizer,
    context: TenantContext,
    credential_fingerprint: str,
    execution: TradeExecution,
    delegated_ref: dict[str, Any],
    audit: dict[str, Any],
) -> VerifiedExecutionAuthorization | dict[str, Any]:
    try:
        result = authorizer.authorize(
            context=context,
            credential_fingerprint=credential_fingerprint,
            execution=execution,
            delegated_ref=delegated_ref,
        )
    except ExecutionAuthorizationError as exc:
        return error_response(exc.code, exc.safe_message(), audit=audit)
    except Exception as exc:
        return error_response(
            "execution_authorization_failed",
            "Delegated execution authorization failed.",
            details={"reason": exc.__class__.__name__},
            audit=audit,
        )
    if not isinstance(result, VerifiedExecutionAuthorization):
        return error_response(
            "execution_authorization_invalid",
            "Delegated execution authorizer returned an invalid result.",
            audit=audit,
        )
    return result


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
    if (
        action == "update_leverage"
        and not _has_explicit_leverage(arguments)
        and str(arguments.get("margin_mode") or "").strip().lower() == "isolated"
        and not arguments.get("position_side")
    ):
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


def _has_explicit_leverage(arguments: dict[str, Any]) -> bool:
    return any(field in arguments for field in _EXPLICIT_LEVERAGE_FIELDS)


def _leverage_required_fields(arguments: dict[str, Any]) -> tuple[str, ...]:
    if _has_explicit_leverage(arguments):
        return _LEVERAGE_BASE_REQUIRED_FIELDS
    return (*_LEVERAGE_BASE_REQUIRED_FIELDS, "margin_mode", "leverage")


def _with_clarification(result: dict[str, Any]) -> dict[str, Any]:
    error = result.get("error")
    if not isinstance(error, dict) or error.get("code") != "missing_fields":
        return result
    details = error.get("details")
    fields = details.get("fields") if isinstance(details, dict) else None
    if not isinstance(fields, list) or not fields:
        return result
    result["clarification_required"] = True
    field_list = ", ".join(str(field) for field in fields)
    result["clarification_prompt"] = f"Please provide the following missing fields: {field_list}."
    return result


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
