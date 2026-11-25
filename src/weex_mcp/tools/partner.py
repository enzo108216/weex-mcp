from __future__ import annotations

from typing import Any

from ..adapters.partner import PartnerAdapter, PartnerAdapterError
from ..credential_context import CredentialContext, CredentialError
from ..response import error_response, ok_response
from ..secret_provider import SecretProviderError
from ..tenant_context import TenantContext

_PARTNER_ERROR_MESSAGES = {
    "partner_authentication_error": "WEEX Partner authentication failed.",
    "partner_permission_error": "WEEX Partner permission validation failed.",
    "partner_rate_limit": "WEEX Partner rate limit was reached.",
    "partner_validation_error": "WEEX Partner request validation failed.",
    "partner_upstream_error": "WEEX Partner upstream service failed.",
    "partner_transport_error": "WEEX Partner transport failed.",
    "partner_response_schema_error": "WEEX Partner response schema validation failed.",
    "partner_redirect_forbidden": "WEEX Partner redirect was rejected.",
    "partner_output_redaction_failed": "WEEX Partner output safety validation failed.",
    "expected_partner_environment_mismatch": "Partner environment did not match the expected environment.",
    "partner_test_time_binding_required": "Partner test null-time binding was not satisfied.",
    "partner_adapter_error": "WEEX Partner adapter failed.",
}
_CREDENTIAL_ERROR_MESSAGES = {
    "secret_provider_unconfigured": "Secret provider is not configured for private WEEX API calls.",
    "credential_invalid": "Credential provider rejected the credential context.",
    "credential_ref_not_found": "Credential reference was not found.",
    "permission_denied": "Credential does not allow the required permission.",
    "credential_resolution_failed": "Credential provider failed to resolve credentials.",
}


def get_affiliate_uids(
    arguments: dict[str, Any],
    context: TenantContext,
    *,
    adapter: PartnerAdapter,
) -> dict[str, Any]:
    return _call_partner_adapter(
        arguments,
        context,
        required_fields=(),
        call=lambda credential_argument: adapter.get_affiliate_uids(
            uid=arguments.get("uid"),
            start_time=arguments.get("start_time"),
            end_time=arguments.get("end_time"),
            page=arguments.get("page"),
            page_size=arguments.get("page_size"),
            expected_environment=arguments.get("expected_environment"),
            credential_argument=credential_argument,
            context=context,
        ),
    )


def get_channel_user_trade_and_asset(
    arguments: dict[str, Any],
    context: TenantContext,
    *,
    adapter: PartnerAdapter,
) -> dict[str, Any]:
    return _call_partner_adapter(
        arguments,
        context,
        required_fields=(),
        call=lambda credential_argument: adapter.get_channel_user_trade_and_asset(
            uid=arguments.get("uid"),
            start_time=arguments.get("start_time"),
            end_time=arguments.get("end_time"),
            page=arguments.get("page"),
            page_size=arguments.get("page_size"),
            expected_environment=arguments.get("expected_environment"),
            credential_argument=credential_argument,
            context=context,
        ),
    )


def get_affiliate_commission(
    arguments: dict[str, Any],
    context: TenantContext,
    *,
    adapter: PartnerAdapter,
) -> dict[str, Any]:
    return _call_partner_adapter(
        arguments,
        context,
        required_fields=(),
        call=lambda credential_argument: adapter.get_affiliate_commission(
            uid=arguments.get("uid"),
            start_time=arguments.get("start_time"),
            end_time=arguments.get("end_time"),
            coin=arguments.get("coin"),
            product_type=arguments.get("product_type"),
            page=arguments.get("page"),
            page_size=arguments.get("page_size"),
            expected_environment=arguments.get("expected_environment"),
            credential_argument=credential_argument,
            context=context,
        ),
    )


def query_sub_channel_transactions(
    arguments: dict[str, Any],
    context: TenantContext,
    *,
    adapter: PartnerAdapter,
) -> dict[str, Any]:
    return _call_partner_adapter(
        arguments,
        context,
        required_fields=("product_type", "start_time", "end_time"),
        call=lambda credential_argument: adapter.query_sub_channel_transactions(
            sub_uid=arguments.get("sub_uid"),
            start_time=arguments.get("start_time"),
            end_time=arguments.get("end_time"),
            product_type=_required_text(arguments, "product_type"),
            page_num=arguments.get("page_num"),
            page_size=arguments.get("page_size"),
            expected_environment=arguments.get("expected_environment"),
            credential_argument=credential_argument,
            context=context,
        ),
    )


def verify_referrals(
    arguments: dict[str, Any],
    context: TenantContext,
    *,
    adapter: PartnerAdapter,
) -> dict[str, Any]:
    return _call_partner_adapter(
        arguments,
        context,
        required_fields=("user_ids",),
        call=lambda credential_argument: adapter.verify_referrals(
            user_ids=_required_text_list(arguments, "user_ids"),
            expected_environment=arguments.get("expected_environment"),
            credential_argument=credential_argument,
            context=context,
        ),
    )


def get_referral_assets(
    arguments: dict[str, Any],
    context: TenantContext,
    *,
    adapter: PartnerAdapter,
) -> dict[str, Any]:
    return _call_partner_adapter(
        arguments,
        context,
        required_fields=("user_id", "start_time", "end_time"),
        call=lambda credential_argument: adapter.get_referral_assets(
            user_id=_required_text(arguments, "user_id"),
            start_time=_required_text(arguments, "start_time"),
            end_time=_required_text(arguments, "end_time"),
            expected_environment=arguments.get("expected_environment"),
            credential_argument=credential_argument,
            context=context,
        ),
    )


def get_referral_deal_data(
    arguments: dict[str, Any],
    context: TenantContext,
    *,
    adapter: PartnerAdapter,
) -> dict[str, Any]:
    return _call_partner_adapter(
        arguments,
        context,
        required_fields=("start_time", "end_time"),
        call=lambda credential_argument: adapter.get_referral_deal_data(
            user_ids=arguments.get("user_ids"),
            start_time=_required_text(arguments, "start_time"),
            end_time=_required_text(arguments, "end_time"),
            expected_environment=arguments.get("expected_environment"),
            credential_argument=credential_argument,
            context=context,
        ),
    )


def _call_partner_adapter(
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
    missing = [field for field in required_fields if field not in arguments]
    if missing:
        return error_response(
            "missing_fields",
            "Partner query is missing required fields.",
            details={"fields": missing},
            audit=audit,
        )
    try:
        data = call(dict(arguments["credential"]))
    except SecretProviderError as exc:
        code = (
            exc.code
            if isinstance(exc.code, str) and exc.code in _CREDENTIAL_ERROR_MESSAGES
            else "credential_resolution_failed"
        )
        return error_response(code, _CREDENTIAL_ERROR_MESSAGES[code], audit=audit)
    except PartnerAdapterError as exc:
        code = (
            exc.code
            if isinstance(exc.code, str) and exc.code in _PARTNER_ERROR_MESSAGES
            else "partner_adapter_error"
        )
        return error_response(
            code,
            _PARTNER_ERROR_MESSAGES[code],
            details=exc.safe_details(),
            audit=audit,
        )
    except ValueError:
        return error_response(
            "invalid_arguments",
            "Partner query arguments are invalid.",
            details={"reason": "ValueError"},
            audit=audit,
        )
    except Exception:
        return error_response(
            "partner_adapter_error",
            "WEEX Partner adapter failed.",
            audit=audit,
        )
    return ok_response(data=data, audit=audit)


def _required_text(arguments: dict[str, Any], field: str) -> str:
    value = arguments.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string.")
    return value


def _required_text_list(arguments: dict[str, Any], field: str) -> list[str]:
    value = arguments.get(field)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field} must be an array of strings.")
    return value
