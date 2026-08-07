from __future__ import annotations

import re
from typing import Any


class WeexRestBusinessError(RuntimeError):
    def __init__(self, *, code: str, message: str, payload: Any | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.payload = payload

    def safe_details(self) -> dict[str, str]:
        code = self.code if re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", self.code) else "upstream_error"
        return {"upstream_code": code}


SUCCESS_CODES = {"0", "00000", "SUCCESS", "success", "OK", "ok"}

# WEEX endpoints do not use one uniform envelope.  Some return a top-level
# status while others put the actual business status inside ``data`` (and a
# few SDK-compatible responses nest that container one level further).  Only
# these known business containers are traversed so ordinary resource fields
# named ``code`` do not accidentally become transport errors.
_BUSINESS_CONTAINER_KEYS = ("data", "result", "response", "payload", "body")


def raise_for_weex_business_error(payload: Any) -> None:
    if not isinstance(payload, dict):
        return
    error = _find_business_error(payload)
    if error is None:
        return
    code, message = error
    raise WeexRestBusinessError(code=code, message=message, payload=payload)


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
