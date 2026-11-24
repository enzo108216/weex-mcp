from __future__ import annotations

from typing import Any

from ..adapters.broker_oauth import BrokerOAuthAdapter, BrokerOAuthError
from ..response import error_response, sensitive_ok_response

_BROKER_ERROR_MESSAGES = {
    "token_exchange_failed": "WEEX Broker token exchange failed.",
    "api_key_creation_failed": "WEEX Broker API key creation failed.",
    "api_key_already_bound": "The WEEX user already has an API key bound to this Broker client.",
    "api_key_creation_result_unknown": (
        "WEEX Broker API key creation result is unknown; do not retry automatically."
    ),
}


def generate_authorization_url(
    arguments: dict[str, Any],
    *,
    adapter: BrokerOAuthAdapter,
) -> dict[str, Any]:
    missing = _missing_fields(arguments, ("client_id", "redirect_uri"))
    if missing:
        return error_response(
            "missing_fields",
            "Broker OAuth authorization URL request is missing required fields.",
            details={"fields": missing},
        )
    try:
        data = adapter.generate_authorization_url(
            client_id=str(arguments["client_id"]),
            redirect_uri=str(arguments["redirect_uri"]),
            oauth_security=(
                dict(arguments["oauth_security"])
                if isinstance(arguments.get("oauth_security"), dict)
                else None
            ),
        )
        security = data.get("oauth_security")
        if not isinstance(security, dict):
            raise RuntimeError("Broker OAuth adapter returned an invalid security group.")
        projected_data = {
            "authorization_url": _result_text(data, "authorization_url"),
            "client_id": _result_text(data, "client_id"),
            "redirect_uri": _result_text(data, "redirect_uri"),
            "oauth_security": {
                "state": _result_text(security, "state"),
                "code_verifier": _result_text(security, "code_verifier"),
                "code_challenge": _result_text(security, "code_challenge"),
                "code_challenge_method": _result_text(security, "code_challenge_method"),
            },
        }
    except ValueError as exc:
        return error_response("invalid_arguments", str(exc))
    except Exception as exc:
        return error_response(
            "upstream_error",
            "Broker OAuth authorization URL generation failed.",
            details={"reason": exc.__class__.__name__},
        )
    return sensitive_ok_response(data=projected_data)


def exchange_code_for_api_key(
    arguments: dict[str, Any],
    *,
    adapter: BrokerOAuthAdapter,
) -> dict[str, Any]:
    required = ("client_id", "client_secret", "redirect_uri", "code", "code_verifier")
    missing = _missing_fields(arguments, required)
    if missing:
        return error_response(
            "missing_fields",
            "Broker OAuth code exchange is missing required fields.",
            details={"fields": missing},
        )
    try:
        credential = adapter.exchange_code_for_api_key(
            client_id=str(arguments["client_id"]),
            client_secret=str(arguments["client_secret"]),
            redirect_uri=str(arguments["redirect_uri"]),
            code=str(arguments["code"]),
            code_verifier=str(arguments["code_verifier"]),
        )
        credential_fields = ("api_key", "api_secret", "api_passphrase")
        if not isinstance(credential, dict) or any(
            not isinstance(credential.get(field), str) or not credential[field]
            for field in credential_fields
        ):
            return error_response(
                "api_key_creation_result_unknown",
                _BROKER_ERROR_MESSAGES["api_key_creation_result_unknown"],
                details={"stage": "api_key_creation"},
            )
    except BrokerOAuthError as exc:
        return error_response(
            exc.code,
            _BROKER_ERROR_MESSAGES.get(exc.code, "WEEX Broker OAuth request failed."),
            details=exc.safe_details(),
        )
    except ValueError as exc:
        return error_response("invalid_arguments", str(exc))
    except Exception as exc:
        return error_response(
            "upstream_error",
            "Broker OAuth adapter failed.",
            details={"reason": exc.__class__.__name__},
        )
    return sensitive_ok_response(
        data={
            "credential": {
                "type": "plaintext",
                "api_key": credential["api_key"],
                "api_secret": credential["api_secret"],
                "api_passphrase": credential["api_passphrase"],
            }
        }
    )


def _missing_fields(arguments: dict[str, Any], fields: tuple[str, ...]) -> list[str]:
    return [field for field in fields if not arguments.get(field)]


def _result_text(value: dict[str, Any], field: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item:
        raise RuntimeError(f"Broker OAuth adapter result is missing {field}.")
    return item
