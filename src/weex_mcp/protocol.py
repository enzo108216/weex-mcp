from __future__ import annotations

from typing import Any

from .response import jsonrpc_error


def validate_modern_request_metadata(
    request: Any,
    *,
    modern_version: str,
    mismatch_is_header_error: bool = False,
) -> dict[str, Any] | None:
    """Validate transport-independent metadata required by modern MCP requests."""

    if not isinstance(request, dict):
        return None
    request_id = request.get("id")
    params = request.get("params")
    if not isinstance(params, dict):
        return jsonrpc_error(
            request_id,
            -32602,
            "protocol_version_missing: Request params must contain modern protocol metadata.",
        )
    metadata = params.get("_meta")
    if not isinstance(metadata, dict):
        return jsonrpc_error(
            request_id,
            -32602,
            "protocol_version_missing: Request metadata must declare "
            "io.modelcontextprotocol/protocolVersion.",
        )
    body_version = metadata.get("io.modelcontextprotocol/protocolVersion")
    if not isinstance(body_version, str):
        return jsonrpc_error(
            request_id,
            -32602,
            "protocol_version_missing: Request metadata must declare "
            "io.modelcontextprotocol/protocolVersion.",
        )
    if body_version != modern_version:
        if mismatch_is_header_error:
            return jsonrpc_error(
                request_id,
                -32020,
                "header_mismatch: MCP-Protocol-Version header does not match request metadata.",
                data={"header": modern_version},
            )
        return jsonrpc_error(
            request_id,
            -32022,
            "unsupported_protocol_version: Unsupported MCP protocol version.",
            data={"supported": [modern_version], "requested": body_version},
        )
    client_capabilities = metadata.get("io.modelcontextprotocol/clientCapabilities")
    if not isinstance(client_capabilities, dict):
        return jsonrpc_error(
            request_id,
            -32602,
            "client_capabilities_missing: Request metadata must declare "
            "io.modelcontextprotocol/clientCapabilities.",
        )
    return None
