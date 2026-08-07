# WEEX MCP

WEEX MCP is a Python 3.11+ implementation of Model Context Protocol (MCP)
tools for WEEX market data, account information, and controlled trade
execution. The package supports both local integration and a standalone HTTP
service while maintaining the same tool definitions and request contracts.

The core package has no mandatory runtime dependencies. Optional dependencies
are provided for the standalone ASGI server and development verification.

## Capabilities

- 17 tools covering market, account, and trade operations.
- Local stdio transport for MCP client integration.
- Embeddable service and ASGI application factories for host applications.
- Standalone Streamable HTTP deployment through Uvicorn.
- Strict JSON Schema validation for tool arguments and structured responses.
- Request-scoped credential handling without credential persistence.
- Stateless, signed approval tokens for two-phase trade execution.
- Redacted audit metadata for authenticated account and trade operations.

Trade operations are disabled by default and must be enabled explicitly. The
service does not persist API credentials, approval state, idempotency results,
or user sessions.

## Installation

Install the core package:

```bash
python -m pip install .
```

Install the standalone server dependencies:

```bash
python -m pip install '.[server]'
```

Install the development verification dependencies:

```bash
python -m pip install -e '.[dev,server]'
```

## Runtime modes

### Local stdio transport

Start the newline-delimited JSON-RPC stdio server:

```bash
weex-mcp stdio
```

An MCP client can register `weex-mcp` as the command and `stdio` as its
argument. Protocol responses are written to standard output; diagnostics are
written to standard error.

### Embedded service

Applications can use the transport-independent service directly:

```python
from weex_mcp.service import build_default_service

service = build_default_service(trade_enabled=False)
```

Applications with an ASGI host can construct the HTTP application separately:

```python
from weex_mcp.asgi_app import build_asgi_app
from weex_mcp.service import build_default_service

service = build_default_service(trade_enabled=False)
app = build_asgi_app(service=service)
```

Explicit `Settings` should be supplied when multiple application instances
must share protocol, authentication, or approval-token configuration.

### Standalone HTTP service

Cloud mode requires an approval-token signing secret and a service bearer
token before the listening socket is opened:

```bash
export WEEX_APPROVAL_TOKEN_SECRET='replace-with-at-least-32-random-bytes'
export WEEX_SERVICE_AUTH_TOKEN='replace-with-a-random-service-token'
export WEEX_TRADE_ENABLED='false'
weex-mcp serve --host 0.0.0.0 --port 8080 --workers 1
```

`GET /healthz` provides liveness status. `GET /readyz` provides readiness
status. Complete configuration, container, scaling, rotation, and rollback
requirements are documented in [DEPLOYMENT.md](DEPLOYMENT.md).

## Protocol compatibility

The HTTP and stdio transports support the following MCP protocol versions:

| Protocol version | Compatibility mode | Negotiation |
| --- | --- | --- |
| `2025-06-18` | Codex-compatible legacy mode | `initialize` request |
| `2025-11-25` | Claude Code-compatible legacy mode | `initialize` request |
| `2026-07-28` | Current project contract | Protocol header and request metadata |

Legacy responses use the response shape associated with the negotiated
protocol. The `2026-07-28` contract additionally validates
`MCP-Protocol-Version`, request metadata, result type, cache scope, and TTL
metadata. HTTP protocol selection is evaluated for each request and is not
stored as user session state.

## Tool catalog

| Module | Tools | Access model |
| --- | --- | --- |
| Market | `market_get_symbol_price`, `market_get_depth`, `market_get_klines`, `market_get_exchange_info` | Public market data |
| Account | `account_get_balance`, `account_get_positions`, `account_get_orders`, `account_get_trades`, `account_get_bills` | Request-scoped `USER_DATA` credential |
| Trade | `trade_preview_order`, `trade_confirm_order`, `trade_preview_cancel_order`, `trade_confirm_cancel_order`, `trade_preview_leverage`, `trade_confirm_leverage`, `trade_preview_margin_mode`, `trade_confirm_margin_mode` | Explicit enablement and two-phase confirmation |

Every tool publishes an input schema, output schema, client-facing title,
module metadata, and MCP risk annotations. Invalid arguments are rejected
before an adapter operation is executed.

## Credential handling

Account and trade requests may provide a complete WEEX credential in the tool
arguments:

```python
credential = {
    "type": "plaintext",
    "api_key": "...",
    "api_secret": "...",
    "api_passphrase": "...",
}
```

Plaintext credentials are resolved in memory for the current request only.
Credential values are excluded from responses and audit records. Credentials
must not be placed in URLs or query parameters. HTTP deployments must use TLS
and must disable request-body capture in proxies, application monitoring,
tracing, and error-reporting systems.

The `credential_ref` and `encrypted_envelope` forms remain extension points
for hosts that provide a compatible secret resolver. Tenant, user, and session
headers are optional; absent values resolve to an anonymous internal context.

## Trade authorization model

Trade operations use a stateless preview-and-confirm sequence:

1. A preview request validates credentials, permissions, market rules, and
   action-specific parameters.
2. The response returns a short-lived, HMAC-signed `approval_token`, an
   execution summary, and risk information.
3. A confirmation request resubmits the complete action parameters, the same
   request-scoped credential, the approval token, and explicit confirmation.
4. The service resolves the credential again, repeats all pre-execution
   validation, verifies the signed token binding, and then invokes the write
   adapter.

For compatibility with the current tool schema, `confirmation.reply_text`
uses the exact protocol value `确认`. This value is part of the request contract
and is not a free-form user message.

Approval tokens can be replayed until expiry. Order operations therefore
require a stable `client_order_id` for exchange-side correlation and recovery.
The package does not claim exactly-once execution semantics.

## Verification

The repository defines the following local checks:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest discover -s tests
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m compileall -q src tests scripts
python -m build --wheel --no-isolation
ruff check src tests scripts
mypy src/weex_mcp
PYTHONPATH=src python3 -m weex_mcp --smoke
PYTHONPATH=src python3 -m weex_mcp serve --help
```

Unit tests use injected transports and adapters. Network-dependent demo smoke
execution requires explicit authorization and is not part of the default test
suite.

## Operational boundary

The package provides MCP protocol handling, strict configuration validation,
request-scoped credential resolution, WEEX REST adapters, health endpoints,
and a container entry point. External infrastructure remains responsible for
TLS termination, secret distribution, gateway policy, request-body log
suppression, monitoring, alerting, deployment rollout, and reconciliation of
unknown trade outcomes.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE).
