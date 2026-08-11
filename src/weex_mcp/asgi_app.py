from __future__ import annotations

import asyncio
import base64
import binascii
import hmac
import json
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .http_app import handle_mcp_post
from .response import error_response, jsonrpc_error, ok_response
from .service import LEGACY_PROTOCOL_VERSIONS, McpService, build_default_service
from .settings import Settings
from .tenant_context import TenantContext

AsgiReceive = Callable[[], Awaitable[dict[str, Any]]]
AsgiSend = Callable[[dict[str, Any]], Awaitable[None]]
ContextFactory = Callable[[dict[str, Any]], TenantContext]


class TenantContextError(ValueError):
    pass


class ClientDisconnected(Exception):
    """Raised when the peer disconnects before a complete request body arrives."""


def build_asgi_app(
    *,
    service: McpService | None = None,
    settings: Settings | None = None,
    context_factory: ContextFactory | None = None,
    max_body_bytes: int | None = None,
) -> Callable[[dict[str, Any], AsgiReceive, AsgiSend], Awaitable[None]]:
    active_settings = settings or (service.settings if service is not None else Settings())
    active_service = service or build_default_service(settings=active_settings)
    active_context_factory = context_factory or tenant_context_from_headers
    active_max_body_bytes = max_body_bytes if max_body_bytes is not None else active_settings.max_request_bytes

    async def app(scope: dict[str, Any], receive: AsgiReceive, send: AsgiSend) -> None:
        if scope.get("type") != "http":
            await _send_json(send, 500, error_response("unsupported_scope", "Only HTTP ASGI scopes are supported."))
            return

        method = str(scope.get("method") or "").upper()
        path = str(scope.get("path") or "")
        if path == "/healthz" and method == "GET":
            await _send_json(send, 200, ok_response(service=active_settings.server_name, version=active_settings.server_version))
            return
        if path == "/readyz" and method == "GET":
            await _send_json(
                send,
                200,
                ok_response(ready=True, service=active_settings.server_name, version=active_settings.server_version),
            )
            return
        if path != "/mcp":
            await _send_json(send, 404, error_response("not_found", "Route not found."))
            return
        if method != "POST":
            await _send_json(send, 405, error_response("method_not_allowed", "MCP endpoint only accepts POST."))
            return

        headers = _headers(scope)
        origin = headers.get("origin")
        if origin is not None and not _origin_is_allowed(origin, active_settings.allowed_origins):
            await _send_json(send, 403, jsonrpc_error(None, -32021, "origin_forbidden: Origin is not allowed."))
            return
        if active_settings.service_auth_token:
            expected = f"Bearer {active_settings.service_auth_token}"
            if not hmac.compare_digest(headers.get("authorization", ""), expected):
                await _send_json(send, 401, error_response("service_auth_failed", "Service authorization failed."))
                return

        try:
            context = active_context_factory(scope)
        except TenantContextError as exc:
            await _send_json(send, 401, error_response("tenant_context_missing", str(exc)))
            return

        try:
            body = await _read_body(receive, max_body_bytes=active_max_body_bytes)
        except ClientDisconnected:
            return
        except ValueError:
            await _send_json(send, 413, error_response("request_too_large", "Request body exceeds configured size limit."))
            return

        query_params = {
            key: values
            for key, values in parse_qs(
                (scope.get("query_string") or b"").decode("utf-8", "replace"),
                keep_blank_values=True,
            ).items()
        }
        if query_params:
            await _send_json(
                send,
                400,
                error_response(
                    "credential_in_query",
                    "Credential material is not allowed in URL query parameters.",
                ),
            )
            return

        protocol_version, protocol_error = _resolve_protocol_version(
            body,
            headers,
            modern_version=active_settings.protocol_version,
        )
        if protocol_error is not None:
            await _send_json(send, 400, protocol_error)
            return

        response = await asyncio.to_thread(
            handle_mcp_post,
            body,
            context=context,
            query_params=query_params,
            service=active_service,
            protocol_version=protocol_version,
        )
        await _send_raw_json(send, response.status, response.body, response.headers)

    return app


def _resolve_protocol_version(
    body: bytes,
    headers: dict[str, str],
    *,
    modern_version: str,
) -> tuple[str, dict[str, Any] | None]:
    header_version = headers.get("mcp-protocol-version")
    request: Any = None
    try:
        request = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        pass

    requested_version: str | None = None
    if isinstance(request, dict) and request.get("method") == "initialize":
        params = request.get("params")
        if isinstance(params, dict) and isinstance(params.get("protocolVersion"), str):
            requested_version = params["protocolVersion"]

    if header_version is None:
        if not isinstance(request, dict):
            return modern_version, None
        if requested_version in LEGACY_PROTOCOL_VERSIONS:
            return requested_version, None
        request_id = request.get("id") if isinstance(request, dict) else None
        return modern_version, jsonrpc_error(
            request_id,
            -32020,
            "header_mismatch: MCP-Protocol-Version header is required except during legacy initialize.",
        )

    if header_version in LEGACY_PROTOCOL_VERSIONS:
        request_id = request.get("id") if isinstance(request, dict) else None
        if requested_version is not None and requested_version != header_version:
            return header_version, jsonrpc_error(
                request_id,
                -32020,
                "header_mismatch: MCP-Protocol-Version header does not match initialize protocolVersion.",
                data={"header": header_version},
            )
        return header_version, None

    if header_version != modern_version:
        request_id = request.get("id") if isinstance(request, dict) else None
        return header_version, jsonrpc_error(
            request_id,
            -32022,
            "unsupported_protocol_version: Unsupported MCP protocol version.",
            data={"supported": [modern_version], "requested": header_version},
        )

    metadata_error = _validate_protocol_metadata(body, header_version, headers)
    return header_version, metadata_error


def _validate_protocol_metadata(body: bytes, header_version: str, headers: dict[str, str]) -> dict[str, Any] | None:
    """Validate modern per-request protocol metadata for Streamable HTTP.

    JSON decoding errors remain the responsibility of ``handle_mcp_post`` so it
    can produce the standard JSON-RPC parse-error response.
    """
    try:
        request = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(request, dict):
        return None
    request_id = request.get("id")
    method = request.get("method")
    params = request.get("params")
    if not isinstance(params, dict):
        return jsonrpc_error(
            request_id,
            -32602,
            "protocol_version_missing: Request params must contain modern protocol metadata.",
        )
    metadata = params.get("_meta")
    if not isinstance(metadata, dict):
        return jsonrpc_error(request_id, -32602, "protocol_version_missing: Request metadata must declare io.modelcontextprotocol/protocolVersion.")
    body_version = metadata.get("io.modelcontextprotocol/protocolVersion")
    if body_version != header_version:
        return jsonrpc_error(
            request_id,
            -32020,
            "header_mismatch: MCP-Protocol-Version header does not match request metadata.",
            data={"header": header_version},
        )
    client_capabilities = metadata.get("io.modelcontextprotocol/clientCapabilities")
    if not isinstance(client_capabilities, dict):
        return jsonrpc_error(
            request_id,
            -32602,
            "client_capabilities_missing: Request metadata must declare io.modelcontextprotocol/clientCapabilities.",
        )
    mirrored_method = _decode_mirrored_header(headers.get("mcp-method"), allow_base64=False)
    if mirrored_method is None or not isinstance(method, str) or mirrored_method != method:
        return jsonrpc_error(request_id, -32020, "header_mismatch: Mcp-Method header is missing, invalid, or does not match the request body.")
    if method in {"tools/call", "resources/read", "prompts/get"}:
        name = params.get("name") if method != "resources/read" else params.get("uri")
        mirrored_name = _decode_mirrored_header(headers.get("mcp-name"), allow_base64=True)
        if mirrored_name is None or not isinstance(name, str) or mirrored_name != name:
            return jsonrpc_error(request_id, -32020, "header_mismatch: Mcp-Name header is missing, invalid, or does not match the request body.")
    return None


def _decode_mirrored_header(value: str | None, *, allow_base64: bool) -> str | None:
    if value is None:
        return None
    if allow_base64 and value.startswith("=?base64?") and value.endswith("?="):
        encoded = value[len("=?base64?"):-2]
        try:
            decoded = base64.b64decode(encoded, validate=True)
            return decoded.decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            return None
    if value != value.strip() or any(ord(character) < 0x20 or ord(character) > 0x7E for character in value):
        return None
    return value


def _origin_is_allowed(origin: str, configured_origins: tuple[str, ...]) -> bool:
    if origin in configured_origins:
        return True
    try:
        parsed = urlsplit(origin)
        port = parsed.port
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        return False
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        return False
    if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        return False
    return port is None or 1 <= port <= 65535


def tenant_context_from_headers(scope: dict[str, Any]) -> TenantContext:
    headers = _headers(scope)
    tenant_id = headers.get("x-tenant-id")
    user_id = headers.get("x-user-id")
    session_id = headers.get("x-session-id")
    if tenant_id is None and user_id is None and session_id is None:
        return TenantContext(tenant_id="mcp", user_id="anonymous", session_id="request")
    missing = [name for name, value in (("x-tenant-id", tenant_id), ("x-user-id", user_id), ("x-session-id", session_id)) if not value]
    if missing:
        raise TenantContextError(f"Missing required context headers: {', '.join(missing)}.")
    try:
        return TenantContext(tenant_id=str(tenant_id), user_id=str(user_id), session_id=str(session_id))
    except ValueError as exc:
        raise TenantContextError("Tenant context headers are invalid.") from exc


def _headers(scope: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_key, raw_value in scope.get("headers") or []:
        key = raw_key.decode("latin-1").lower()
        value = raw_value.decode("latin-1")
        result[key] = value
    return result


async def _read_body(receive: AsgiReceive, *, max_body_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        message = await receive()
        if message.get("type") == "http.disconnect":
            raise ClientDisconnected
        if message.get("type") != "http.request":
            continue
        chunk = message.get("body") or b""
        total += len(chunk)
        if total > max_body_bytes:
            raise ValueError("request_too_large")
        chunks.append(chunk)
        if not message.get("more_body", False):
            break
    return b"".join(chunks)


async def _send_json(send: AsgiSend, status: int, payload: dict[str, Any]) -> None:
    await _send_raw_json(send, status, json.dumps(payload, ensure_ascii=False, sort_keys=True), {"content-type": "application/json; charset=utf-8"})


async def _send_raw_json(send: AsgiSend, status: int, body: str, headers: dict[str, str]) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(key.lower().encode("latin-1"), value.encode("latin-1")) for key, value in headers.items()],
        }
    )
    await send({"type": "http.response.body", "body": body.encode("utf-8")})
