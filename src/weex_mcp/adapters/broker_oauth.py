from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Callable
from typing import Any, Protocol
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .rest_errors import WeexRestBusinessError, raise_for_weex_business_error

BROKER_AUTHORIZATION_URL = "https://www.weex.com/oauth"
BROKER_GATEWAY_BASE_URL = "https://gateway.weex.com"
TOKEN_PATH = "/v1/oauth/token"
CREATE_API_KEY_PATH = "/v1/oauth/resource/create-api"
_OAUTH_SAFE_CHARACTERS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")

PreparedTransport = Callable[[dict[str, Any], float], bytes]


class BrokerOAuthAdapter(Protocol):
    def generate_authorization_url(
        self,
        *,
        client_id: str,
        redirect_uri: str,
        oauth_security: dict[str, Any] | None,
    ) -> dict[str, Any]:
        ...

    def exchange_code_for_api_key(
        self,
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        code: str,
        code_verifier: str,
    ) -> dict[str, str]:
        ...


class BrokerOAuthError(RuntimeError):
    def __init__(self, code: str, message: str, *, stage: str, upstream_code: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.stage = stage
        self.upstream_code = upstream_code

    def safe_details(self) -> dict[str, str]:
        details = {"stage": self.stage}
        if self.upstream_code:
            details["upstream_code"] = self.upstream_code
        return details


class _BrokerOAuthHttpError(RuntimeError):
    def __init__(self, status: int, body: bytes):
        super().__init__(f"WEEX Broker HTTP response status: {status}")
        self.status = status
        self.body = body


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


_NO_REDIRECT_OPENER = build_opener(_NoRedirectHandler())


class WeexBrokerOAuthAdapter:
    """Stateless adapter for the WEEX Broker OAuth authorization and key exchange."""

    def __init__(
        self,
        *,
        authorization_url: str = BROKER_AUTHORIZATION_URL,
        gateway_base_url: str = BROKER_GATEWAY_BASE_URL,
        timeout_seconds: float = 5.0,
        transport: PreparedTransport | None = None,
        time_func: Callable[[], float] | None = None,
        nonce_func: Callable[[], str] | None = None,
        state_func: Callable[[], str] | None = None,
        verifier_func: Callable[[], str] | None = None,
    ) -> None:
        self.authorization_url = authorization_url.rstrip("?")
        self.gateway_base_url = gateway_base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.transport = transport or _default_transport
        self.time_func = time_func or time.time
        self.nonce_func = nonce_func or (lambda: secrets.token_urlsafe(24))
        self.state_func = state_func or (lambda: secrets.token_urlsafe(24))
        self.verifier_func = verifier_func or (lambda: secrets.token_urlsafe(32))

    def generate_authorization_url(
        self,
        *,
        client_id: str,
        redirect_uri: str,
        oauth_security: dict[str, Any] | None,
    ) -> dict[str, Any]:
        client_id = _require_text(client_id, "client_id")
        redirect_uri = _require_text(redirect_uri, "redirect_uri")

        if oauth_security is None:
            state = self.state_func()
            code_verifier = self.verifier_func()
            code_challenge = _code_challenge(code_verifier)
        else:
            if not isinstance(oauth_security, dict):
                raise ValueError("oauth_security must be an object.")
            required = {"state", "code_verifier", "code_challenge"}
            missing = sorted(required - set(oauth_security))
            unknown = sorted(set(oauth_security) - required)
            if missing or unknown:
                raise ValueError("oauth_security must contain exactly state, code_verifier, and code_challenge.")
            state = str(oauth_security["state"])
            code_verifier = str(oauth_security["code_verifier"])
            code_challenge = str(oauth_security["code_challenge"])

        _validate_oauth_value(state, "state", minimum=22, maximum=128)
        _validate_oauth_value(code_verifier, "code_verifier", minimum=43, maximum=128)
        _validate_oauth_value(code_challenge, "code_challenge", minimum=43, maximum=128)
        expected_challenge = _code_challenge(code_verifier)
        if not hmac.compare_digest(code_challenge, expected_challenge):
            raise ValueError("oauth_security.code_challenge does not match code_verifier using S256.")

        query = urlencode(
            [
                ("clientId", client_id),
                ("responseType", "code"),
                ("scope", "create:apikey"),
                ("redirectUri", redirect_uri),
                ("state", state),
                ("codeChallenge", code_challenge),
                ("codeChallengeMethod", "S256"),
            ]
        )
        return {
            "authorization_url": f"{self.authorization_url}?{query}",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "oauth_security": {
                "state": state,
                "code_verifier": code_verifier,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            },
        }

    def exchange_code_for_api_key(
        self,
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        code: str,
        code_verifier: str,
    ) -> dict[str, str]:
        client_id = _require_text(client_id, "client_id")
        client_secret = _require_text(client_secret, "client_secret")
        redirect_uri = _require_text(redirect_uri, "redirect_uri")
        code = _require_text(code, "code")
        code_verifier = _require_text(code_verifier, "code_verifier")
        _validate_oauth_value(code_verifier, "code_verifier", minimum=43, maximum=128)

        token_body = _compact_json(
            {
                "grantType": "authorization_code",
                "code": code,
                "redirectUri": redirect_uri,
                "codeVerifier": code_verifier,
            }
        )
        timestamp = str(int(self.time_func() * 1000))
        nonce = self.nonce_func()
        signature = _broker_signature(
            client_secret=client_secret,
            timestamp=timestamp,
            nonce=nonce,
            method="POST",
            path=TOKEN_PATH,
            query_string="",
            body=token_body,
        )
        token_request = {
            "method": "POST",
            "url": f"{self.gateway_base_url}{TOKEN_PATH}",
            "headers": {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "weex-mcp/0.1 broker-oauth",
                "Client-Id": client_id,
                "Sign": signature,
                "Timestamp": timestamp,
                "Nonce": nonce,
            },
            "data": token_body.encode("utf-8"),
            "body": token_body,
        }
        try:
            token_payload = _decode_json(self.transport(token_request, self.timeout_seconds))
            raise_for_weex_business_error(token_payload)
            if not _is_explicit_broker_success(token_payload):
                raise BrokerOAuthError(
                    "token_exchange_failed",
                    "WEEX Broker token response did not explicitly report success.",
                    stage="token_exchange",
                )
        except _BrokerOAuthHttpError as exc:
            raise BrokerOAuthError(
                "token_exchange_failed",
                "WEEX Broker token exchange returned a non-success HTTP status.",
                stage="token_exchange",
                upstream_code=_upstream_code_from_body(exc.body),
            ) from exc
        except WeexRestBusinessError as exc:
            raise BrokerOAuthError(
                "token_exchange_failed",
                "WEEX Broker token exchange was rejected.",
                stage="token_exchange",
                upstream_code=exc.safe_details()["upstream_code"],
            ) from exc
        except BrokerOAuthError:
            raise
        except Exception as exc:
            raise BrokerOAuthError(
                "token_exchange_failed",
                "WEEX Broker token exchange failed.",
                stage="token_exchange",
            ) from exc

        access_token = _nested_text(token_payload, "data", "accessToken")
        if not access_token:
            raise BrokerOAuthError(
                "token_exchange_failed",
                "WEEX Broker token response did not contain an access token.",
                stage="token_exchange",
            )

        create_request = {
            "method": "POST",
            "url": f"{self.gateway_base_url}{CREATE_API_KEY_PATH}",
            "headers": {
                "Accept": "application/json",
                "User-Agent": "weex-mcp/0.1 broker-oauth",
                "Authorization": f"Bearer {access_token}",
            },
            "data": None,
            "body": "",
        }
        try:
            create_payload = _decode_json(self.transport(create_request, self.timeout_seconds))
            raise_for_weex_business_error(create_payload)
            if not _is_explicit_broker_success(create_payload):
                raise BrokerOAuthError(
                    "api_key_creation_result_unknown",
                    "WEEX Broker API key creation response did not explicitly report success; do not retry automatically.",
                    stage="api_key_creation",
                )
        except _BrokerOAuthHttpError as exc:
            upstream_code = _upstream_code_from_body(exc.body)
            if 400 <= exc.status < 500 and exc.status != 408:
                code_value = "api_key_already_bound" if upstream_code == "81001" else "api_key_creation_failed"
                message = "WEEX Broker API key creation was rejected."
            else:
                code_value = "api_key_creation_result_unknown"
                message = "WEEX Broker API key creation result is unknown; do not retry automatically."
            raise BrokerOAuthError(
                code_value,
                message,
                stage="api_key_creation",
                upstream_code=upstream_code,
            ) from exc
        except WeexRestBusinessError as exc:
            upstream_code = exc.safe_details()["upstream_code"]
            code_value = "api_key_already_bound" if upstream_code == "81001" else "api_key_creation_failed"
            raise BrokerOAuthError(
                code_value,
                "WEEX Broker API key creation was rejected.",
                stage="api_key_creation",
                upstream_code=upstream_code,
            ) from exc
        except BrokerOAuthError:
            raise
        except Exception as exc:
            raise BrokerOAuthError(
                "api_key_creation_result_unknown",
                "WEEX Broker API key creation result is unknown; do not retry automatically.",
                stage="api_key_creation",
            ) from exc

        api_key = _nested_text(create_payload, "data", "apiKey")
        api_secret = _nested_text(create_payload, "data", "secret")
        api_passphrase = _nested_text(create_payload, "data", "passphrase")
        if not api_key or not api_secret or not api_passphrase:
            raise BrokerOAuthError(
                "api_key_creation_result_unknown",
                "WEEX Broker API key creation response was incomplete; do not retry automatically.",
                stage="api_key_creation",
            )
        return {
            "api_key": api_key,
            "api_secret": api_secret,
            "api_passphrase": api_passphrase,
        }


def _code_challenge(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _broker_signature(
    *,
    client_secret: str,
    timestamp: str,
    nonce: str,
    method: str,
    path: str,
    query_string: str,
    body: str,
) -> str:
    message = f"{timestamp}{nonce}{method.upper()}{path}"
    if query_string:
        message += f"?{query_string}"
    message += body
    digest = hmac.new(client_secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def _validate_oauth_value(value: str, name: str, *, minimum: int, maximum: int) -> None:
    if not minimum <= len(value) <= maximum or any(character not in _OAUTH_SAFE_CHARACTERS for character in value):
        raise ValueError(f"{name} must be {minimum}-{maximum} characters from [A-Z, a-z, 0-9, -, ., _, ~].")


def _require_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} is required.")
    return value


def _compact_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _decode_json(raw: bytes) -> Any:
    return json.loads(raw.decode("utf-8"))


def _nested_text(value: Any, container: str, key: str) -> str:
    if not isinstance(value, dict) or not isinstance(value.get(container), dict):
        return ""
    item = value[container].get(key)
    return item if isinstance(item, str) else ""


def _is_explicit_broker_success(value: Any) -> bool:
    return isinstance(value, dict) and value.get("code") == "00000"


def _upstream_code_from_body(raw: bytes) -> str | None:
    try:
        payload = _decode_json(raw)
        raise_for_weex_business_error(payload)
    except WeexRestBusinessError as exc:
        return exc.safe_details()["upstream_code"]
    except Exception:
        return None
    return None


def _default_transport(prepared: dict[str, Any], timeout_seconds: float) -> bytes:
    request = Request(
        url=prepared["url"],
        method=prepared["method"],
        data=prepared["data"],
        headers=prepared["headers"],
    )
    try:
        with _open_no_redirect(request, timeout_seconds) as response:
            return response.read()
    except HTTPError as exc:
        try:
            body = exc.read()
        finally:
            exc.close()
        raise _BrokerOAuthHttpError(exc.code, body) from exc


def _open_no_redirect(request: Request, timeout_seconds: float) -> Any:
    return _NO_REDIRECT_OPENER.open(request, timeout=timeout_seconds)


__all__ = [
    "BROKER_AUTHORIZATION_URL",
    "BROKER_GATEWAY_BASE_URL",
    "BrokerOAuthAdapter",
    "BrokerOAuthError",
    "WeexBrokerOAuthAdapter",
]
