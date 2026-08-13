from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from .protocol import validate_modern_request_metadata
from .response import jsonrpc_error
from .service import LEGACY_PROTOCOL_VERSIONS, McpService, build_default_service
from .settings import Settings
from .tenant_context import TenantContext


def run_stdio_server(
    *,
    service: McpService | None = None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> int:
    """Serve newline-delimited MCP JSON-RPC without storing business state."""

    active_service = service or build_default_service(settings=Settings.from_environment())
    input_stream = stdin or sys.stdin
    output_stream = stdout or sys.stdout
    protocol_version: str | None = None
    context = TenantContext(tenant_id="mcp", user_id="anonymous", session_id="stdio")

    for raw_line in input_stream:
        if not raw_line.strip():
            continue
        response: dict[str, Any] | None
        try:
            request: Any = json.loads(raw_line)
        except json.JSONDecodeError:
            response = jsonrpc_error(None, -32700, "Parse error.")
        else:
            metadata_error = _modern_metadata_error(
                request,
                protocol_version=protocol_version,
                modern_version=active_service.settings.protocol_version,
            )
            if metadata_error is not None:
                response = None if isinstance(request, dict) and "id" not in request else metadata_error
            else:
                response = active_service.handle_jsonrpc(
                    request,
                    context=context,
                    protocol_version=protocol_version,
                )
            negotiated = _negotiated_protocol_version(request, response)
            if negotiated is not None:
                protocol_version = negotiated
        if response is not None:
            output_stream.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            output_stream.flush()
    return 0


def _negotiated_protocol_version(
    request: Any,
    response: dict[str, Any] | None,
) -> str | None:
    if not isinstance(request, dict) or request.get("method") != "initialize":
        return None
    if not isinstance(response, dict) or not isinstance(response.get("result"), dict):
        return None
    version = response["result"].get("protocolVersion")
    return version if version in LEGACY_PROTOCOL_VERSIONS else None


def _modern_metadata_error(
    request: Any,
    *,
    protocol_version: str | None,
    modern_version: str,
) -> dict[str, Any] | None:
    if protocol_version in LEGACY_PROTOCOL_VERSIONS or _is_legacy_initialize(request):
        return None
    return validate_modern_request_metadata(request, modern_version=modern_version)


def _is_legacy_initialize(request: Any) -> bool:
    if not isinstance(request, dict) or request.get("method") != "initialize":
        return False
    params = request.get("params")
    return isinstance(params, dict) and params.get("protocolVersion") in LEGACY_PROTOCOL_VERSIONS
