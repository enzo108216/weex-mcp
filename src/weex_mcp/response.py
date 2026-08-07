from __future__ import annotations

import json
from typing import Any

from .audit import redact_secrets


def ok_response(**fields: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"ok": True}
    result.update(fields)
    return redact_secrets(result)


def error_response(
    code: str,
    message: str,
    *,
    details: Any | None = None,
    audit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    result: dict[str, Any] = {"ok": False, "error": error}
    if audit:
        result["audit"] = audit
    return redact_secrets(result)


def jsonrpc_result(request_id: Any, result: dict[str, Any], *, redact: bool = True) -> dict[str, Any]:
    # Tool schemas are static contract declarations.  Their property names can
    # legitimately contain words such as ``ciphertext`` or ``signature`` and
    # must not be mistaken for secret values by the payload redactor.
    safe_result = redact_secrets(result) if redact else result
    safe_result.setdefault("resultType", "complete")
    return {"jsonrpc": "2.0", "id": request_id, "result": safe_result}


def jsonrpc_error(request_id: Any, code: int, message: str, *, data: Any | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = redact_secrets(data)
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def mcp_call_tool_result(payload: dict[str, Any]) -> dict[str, Any]:
    safe_payload = redact_secrets(payload)
    result = dict(safe_payload)
    result["structuredContent"] = safe_payload
    result["content"] = [{"type": "text", "text": json.dumps(safe_payload, ensure_ascii=False, sort_keys=True)}]
    result["isError"] = not bool(safe_payload.get("ok"))
    result["resultType"] = "complete"
    return result
