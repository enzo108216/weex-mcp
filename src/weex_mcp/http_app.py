from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .credential_context import CredentialError, reject_secret_in_query
from .response import error_response, jsonrpc_error
from .service import McpService, build_default_service
from .tenant_context import TenantContext


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: str
    headers: dict[str, str] = field(default_factory=lambda: {"content-type": "application/json; charset=utf-8"})


def _json_body(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def handle_mcp_post(
    raw_body: bytes,
    *,
    context: TenantContext,
    query_params: dict[str, Any] | None = None,
    service: McpService | None = None,
    protocol_version: str | None = None,
) -> HttpResponse:
    try:
        reject_secret_in_query(query_params or {})
    except CredentialError as exc:
        return HttpResponse(status=400, body=_json_body(error_response(exc.code, exc.message)))

    try:
        request = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return HttpResponse(status=400, body=_json_body(jsonrpc_error(None, -32700, "Parse error.")))
    if not isinstance(request, dict):
        return HttpResponse(status=400, body=_json_body(jsonrpc_error(None, -32600, "Invalid JSON-RPC request.")))

    active_service = service or build_default_service(trade_enabled=False)
    response = active_service.handle_jsonrpc(
        request,
        context=context,
        protocol_version=protocol_version,
    )
    if response is None:
        return HttpResponse(status=202, body="", headers={"content-length": "0"})
    status = 200
    if (
        isinstance(response.get("error"), dict)
        and response["error"].get("code") == -32601
    ):
        status = 404
    return HttpResponse(status=status, body=_json_body(response))
