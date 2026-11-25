from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from ..audit import redact_secrets


@dataclass(frozen=True)
class WeexHttpResponse:
    """Raw HTTP result used by the built-in transports.

    Injected test/host transports may continue returning plain ``bytes``.
    """

    status: int
    body: bytes


@dataclass(frozen=True)
class WeexRestPayload:
    payload: Any
    http_status: int | None = None


class WeexRestBusinessError(RuntimeError):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        payload: Any | None = None,
        http_status: int | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        if isinstance(payload, WeexRestPayload):
            self.payload = payload.payload
            self.http_status = payload.http_status if http_status is None else http_status
        else:
            self.payload = payload
            self.http_status = http_status

    def safe_details(self) -> dict[str, Any]:
        code = self.code if re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", self.code) else "upstream_error"
        details: dict[str, Any] = {"upstream_code": code}
        if isinstance(self.http_status, int) and not isinstance(self.http_status, bool):
            details["http_status"] = self.http_status
        if self.payload is not None:
            details["upstream_payload"] = redact_secrets(self.payload)
        return details


SUCCESS_CODES = {"0", "00000", "SUCCESS", "success", "OK", "ok"}

# WEEX endpoints do not use one uniform envelope.  Some return a top-level
# status while others put the actual business status inside ``data`` (and a
# few SDK-compatible responses nest that container one level further).  Only
# these known business containers are traversed so ordinary resource fields
# named ``code`` do not accidentally become transport errors.
_BUSINESS_CONTAINER_KEYS = ("data", "result", "response", "payload", "body")


def raise_for_weex_business_error(payload: Any) -> None:
    http_status = payload.http_status if isinstance(payload, WeexRestPayload) else None
    raw_payload = payload.payload if isinstance(payload, WeexRestPayload) else payload
    if http_status is not None and not 200 <= http_status < 300:
        error = _find_business_error(raw_payload)
        if error is not None:
            code, message = error
        else:
            code = f"http_{http_status}"
            message = f"WEEX API returned HTTP {http_status}."
        raise WeexRestBusinessError(
            code=code,
            message=message,
            payload=raw_payload,
            http_status=http_status,
        )
    if not isinstance(raw_payload, dict):
        return
    error = _find_business_error(raw_payload)
    if error is None:
        return
    code, message = error
    raise WeexRestBusinessError(
        code=code,
        message=message,
        payload=raw_payload,
        http_status=http_status,
    )


def decode_json_transport(value: bytes | WeexHttpResponse) -> WeexRestPayload:
    if isinstance(value, WeexHttpResponse):
        body = value.body
        status: int | None = value.status
    elif isinstance(value, bytes):
        body = value
        status = None
    else:
        raise TypeError("WEEX REST transport returned an invalid result.")
    decoded = body.decode("utf-8", errors="replace")
    try:
        payload = json.loads(decoded)
    except json.JSONDecodeError:
        if status is not None:
            code = "invalid_response" if 200 <= status < 300 else f"http_{status}"
            raise WeexRestBusinessError(
                code=code,
                message=(
                    "WEEX API returned an invalid JSON response."
                    if code == "invalid_response"
                    else f"WEEX API returned HTTP {status}."
                ),
                payload={"raw": decoded},
                http_status=status,
            ) from None
        raise
    result = WeexRestPayload(payload=payload, http_status=status)
    raise_for_weex_business_error(result)
    return result


def raw_rest_payload(value: Any) -> Any:
    return value.payload if isinstance(value, WeexRestPayload) else value


def add_http_status(result: dict[str, Any], value: Any) -> dict[str, Any]:
    if isinstance(value, WeexRestPayload) and value.http_status is not None:
        result["http_status"] = value.http_status
    return result


def _find_business_error(value: Any) -> tuple[str, str] | None:
    if isinstance(value, dict):
        if value.get("success") is False:
            return (
                "upstream_rejected",
                str(value.get("msg") or value.get("message") or "WEEX API reported an unsuccessful response."),
            )
        if "code" in value:
            code = str(value.get("code") or "")
            if code not in SUCCESS_CODES:
                return (code, str(value.get("msg") or value.get("message") or "WEEX API returned an error."))
        for key in _BUSINESS_CONTAINER_KEYS:
            if key in value:
                nested_error = _find_business_error(value[key])
                if nested_error is not None:
                    return nested_error
    # Lists are resource containers (orders, trades, depth, etc.).  Their
    # ordinary item fields may legitimately be named ``code``; treating every
    # list item as an envelope would turn valid resources into transport
    # failures.  Endpoint adapters validate resource shape separately.
    return None
