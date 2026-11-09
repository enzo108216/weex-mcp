# WEEX MCP

<!-- mcp-name: io.github.enzo108216/weex-mcp -->

WEEX MCP is a Python 3.11+ implementation of Model Context Protocol (MCP)
tools for WEEX market data, account information, and controlled trade
execution. The package supports both local integration and a standalone HTTP
service while maintaining the same tool definitions and request contracts.

The core package has no mandatory runtime dependencies. Optional dependencies
are provided for the standalone ASGI server and development verification.

## Contents

- [Capabilities](#capabilities)
- [Installation](#installation)
- [Runtime modes](#runtime-modes)
- [Client configuration](#client-configuration)
- [Protocol compatibility](#protocol-compatibility)
- [Tool catalog](#tool-catalog)
- [Credential handling](#credential-handling)
- [Trade authorization model](#trade-authorization-model)
- [Project layout](#project-layout)
- [Development and verification](#development-and-verification)
- [Troubleshooting](#troubleshooting)
- [Known issues](#known-issues)
- [Operational boundary](#operational-boundary)

## Capabilities

- 47 typed tools covering public Market data, read-only Account data, and
  guarded Trade operations (11 Market, 10 Account, and 26 Trade tools).
- Local stdio transport for MCP client integration.
- Embeddable service and ASGI application factories for host applications.
- Standalone Streamable HTTP deployment through Uvicorn.
- Strict JSON Schema validation for tool arguments. Output schemas are
  published and checked for schema validity, with representative response
  conformance tests.
- Request-scoped credential handling without credential persistence.
- Stateless, signed preview IDs for two-phase trade execution.
- Redacted audit metadata for authenticated account and trade operations.

Trade operations are disabled by default and must be enabled explicitly. The
service does not persist API credentials, approval state, idempotency results,
or user sessions.
When Trade is disabled, its 26 tools are omitted from `tools/list`; direct
calls still fail closed with `module_disabled`. Explicit enablement exposes the
full 47-tool catalog.

## Installation

Install the published core package:

```bash
python -m pip install weex-mcp
```

Install the standalone server dependencies:

```bash
python -m pip install 'weex-mcp[server]'
```

Source checkout and editable development installation are covered in
[Development and verification](#development-and-verification).

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
token before the listening socket is opened. The process binds to
`127.0.0.1` by default:

```bash
export WEEX_APPROVAL_TOKEN_SECRET='replace-with-at-least-32-random-bytes'
export WEEX_SERVICE_AUTH_TOKEN='replace-with-at-least-32-random-ascii-characters'
export WEEX_TRADE_ENABLED='false'
weex-mcp serve --port 8080 --workers 1
```

Use `--host 0.0.0.0` or set `WEEX_MCP_BIND_HOST` only when the process must
accept connections through external interfaces. An explicit `--host` takes
precedence over the environment value.

`GET /healthz` provides liveness status. `GET /readyz` provides readiness
status. Complete configuration, container, scaling, rotation, and rollback
requirements are documented in
[DEPLOYMENT.md](https://github.com/weex-labs/weex-mcp/blob/main/DEPLOYMENT.md).

### One-command Docker startup

With Docker Engine and Compose v2 installed, run:

```bash
./scripts/docker-start.sh
```

On first run the script creates a private `.env` with two random service
secrets, keeps Trade disabled, builds the image, starts the container in the
background, and waits for `/readyz`. It never overwrites an existing `.env`
and never generates or stores a WEEX user API credential. The default endpoint
is `http://127.0.0.1:8080/mcp`; the generated service bearer token is in the
local `.env` file. Docker also writes redacted account/trade audit events to
`logs/weex_mcp_audit.log` on the host by mapping the container path configured
by `WEEX_AUDIT_LOG_FILE` (`/var/log/weex-mcp/weex_mcp_audit.log` by default).
The startup script creates `./logs` and records the current host UID/GID in
`.env` so the non-root container process can append to the mounted audit file.
Stop it with:

```bash
docker compose down
```

To use a different host/container port, export `WEEX_MCP_HOST_PORT` and
`WEEX_MCP_PORT` before the first run, or edit the generated `.env`. The default
host binding is loopback (`WEEX_MCP_BIND_HOST=127.0.0.1`). To publish on all
host interfaces, set the bind host on the first run:

```bash
WEEX_MCP_BIND_HOST=0.0.0.0 ./scripts/docker-start.sh
```

To expose only one host interface, use that interface's IP instead:

```bash
WEEX_MCP_BIND_HOST=192.168.1.20 ./scripts/docker-start.sh
```

The first-run value is saved in the generated `.env`. Because the script never
overwrites an existing `.env`, edit `WEEX_MCP_BIND_HOST` there for later runs,
or export a value to override Compose interpolation for a single run.
`0.0.0.0` publishes on every IPv4 interface; it is a bind address, not a URL
that remote clients should use. Clients connect to a reachable DNS name or
host IP and must still send the service Bearer token. Before exposing the
service beyond localhost, provide TLS, firewall or network policy, request-body
log suppression, monitoring, and appropriate gateway traffic controls.

For manual Compose operation, copy `.env.example` to `.env` and run
`docker compose up -d --build --wait` after filling both required service
secret values; the example intentionally leaves them empty so unchanged
placeholder credentials cannot start the service.

`WEEX_SPOT_BASE_URL` and `WEEX_FUTURES_BASE_URL` select approved deployment
upstreams for local, staging, or production use. They must be HTTPS origins
without credentials, paths, query strings, or fragments and are never accepted
inside a tool request. Public Market GET transport timeouts use bounded retry;
Account, Trade, HTTP/business, and response-validation failures do not.

## Client configuration

Install the package in an environment visible to the client before using the
stdio examples. Keep WEEX account credentials out of client configuration;
Account and Trade tools receive them in the individual tool call only.

### Codex

Register the local stdio server in Codex configuration:

```toml
[mcp_servers.weex]
command = "weex-mcp"
args = ["stdio"]
```

Restart or reload the MCP client after changing its configuration. The server
negotiates the Codex-compatible legacy protocol over stdio.

### Claude Code

Add the same stdio command to the project or user MCP configuration:

```json
{
  "mcpServers": {
    "weex": {
      "command": "weex-mcp",
      "args": ["stdio"]
    }
  }
}
```

Claude Code negotiates its supported legacy protocol during `initialize`.

### HTTP MCP clients

For Docker or cloud deployments, configure a Streamable HTTP connection with:

```text
URL: http://127.0.0.1:8080/mcp
Authorization: Bearer <service-auth-token>
```

Client-specific HTTP configuration formats vary. The service bearer token
protects the MCP endpoint; it is separate from the request-scoped WEEX
credential used by Account and Trade tools. Production URLs must use HTTPS.

## Protocol compatibility

The stdio transport supports the current protocol plus legacy `initialize`
compatibility. HTTP supports the current stateless POST contract and legacy
POST `initialize` compatibility; it does not implement the session-oriented
GET behavior of older Streamable HTTP revisions.

| Protocol version | Compatibility mode | Negotiation |
| --- | --- | --- |
| `2025-06-18` | Codex-compatible legacy initialize | stdio and HTTP POST `initialize` |
| `2025-11-25` | Claude Code-compatible legacy initialize | stdio and HTTP POST `initialize` |
| `2026-07-28` | Current project contract | protocol header (HTTP) and request metadata |

Legacy responses use the response shape associated with the negotiated
protocol. The `2026-07-28` contract validates request metadata, result type,
cache scope, and TTL metadata. HTTP additionally validates
`MCP-Protocol-Version`, `Mcp-Method`, and method-specific `Mcp-Name` headers.
HTTP protocol selection is evaluated for each request and is not stored as
user session state. `server/discover` belongs to the current contract and
therefore advertises `2026-07-28`; the two older versions are compatibility
modes reached through `initialize`, not additional current discovery targets.

## Tool catalog

| Module | Tools | Access model |
| --- | --- | --- |
| Market (11) | `market_get_symbol_price`, `market_get_depth`, `market_get_klines`, `market_get_exchange_info`, `market_get_ticker_24h`, `market_get_book_ticker`, `market_get_recent_trades`, `market_get_funding_rate`, `market_get_open_interest`, `market_get_advanced_klines`, `market_get_catalog` | Public market data |
| Account (10) | `account_get_balance`, `account_get_positions`, `account_get_orders`, `account_get_trades`, `account_get_bills`, `account_get_config`, `account_get_commission_rate`, `account_get_symbol_config`, `account_get_conditional_orders`, `account_get_transfer_records` | Request-scoped `USER_DATA` credential |
| Trade (26) | Existing order/cancel/leverage/margin-mode pairs plus `batch_order`, `batch_cancel`, `cancel_scope`, `conditional_order`, `conditional_cancel`, `tp_sl`, `close_positions`, `position_margin`, and `auto_append_margin` preview/confirm pairs | Explicit enablement and two-phase confirmation |

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

When `WEEX_AUDIT_LOG_FILE` is set, the default service appends local audit
records as JSON Lines. Each line is one redacted account or trade tool event
with fields such as tool name, module, success status, request identity, and
credential fingerprint; raw API keys, secrets, passphrases, and credential refs
are not written. The Docker Compose configuration sets this variable to
`/var/log/weex-mcp/weex_mcp_audit.log` and mounts it to
`logs/weex_mcp_audit.log` on the host. Without `WEEX_AUDIT_LOG_FILE`, audit
events are emitted through the `weex_mcp.audit` Python logger instead of being
written by the MCP to a file.

The `credential_ref` and `encrypted_envelope` forms remain extension points
for hosts that provide a compatible secret resolver. Tenant, user, and session
headers are optional; absent values resolve to an anonymous internal context.

## Trade authorization model

Trade operations use a stateless preview-and-confirm sequence:

1. A preview request validates required fields, market rules, and action-specific
   parameters without private network access. It then authenticates the
   request-scoped key through a signed, read-only WEEX request and requires
   `canTrade=true`. Spot uses `GET /api/v3/account`; futures live and demo use
   `GET /capi/v3/account/accountConfig`.
2. The response returns a short-lived, HMAC-signed `preview_id`, an
   execution summary, and risk information.
3. A confirmation request resubmits the complete action parameters, the same
   request-scoped credential, the preview ID, and explicit confirmation. During
   client migration, confirm tools also accept the deprecated `approval_token`
   alias when `preview_id` is not present.
4. The service first validates the token signature and expiry, then resolves
   the credential again, repeats all pre-execution validation (including the
   read-only `canTrade` probe), compares the token binding, and only then
   invokes the write adapter.

The optional `credential.permissions` request field can only narrow a call; it
does not prove what WEEX granted to the API key. A failed authentication,
`canTrade=false`, or malformed permission response never produces a preview
ID. Preview itself sends no private write request.

Explicit confirmation uses the language-independent object
`{"accepted": true}`. Confirmation is still bound to the signed preview and
does not replace the requirement to display the preview to the user.

Preview IDs can be replayed until expiry. Order operations therefore
require a stable `client_order_id` for exchange-side correlation and recovery.
The package does not claim exactly-once execution semantics.

MCP tool arguments use stable snake_case names for callers, while the REST
adapters translate them to WEEX's official request fields before signing. For
example, `client_order_id` maps to `newClientOrderId`, `position_side` maps to
`positionSide`, `client_algo_id` maps to `clientAlgoId`, and
`trigger_price_type` maps to `triggerPriceType`. Safety fields such as
`scope`, `acknowledge_all_symbols`, `acknowledge_all_positions`, and
`acknowledge_full_position` are MCP-only guard fields; they are never forwarded
to WEEX.

## Project layout

```text
src/weex_mcp/       Core service, transports, adapters, schemas, and tools
tests/              Unit, protocol, package, and runtime contract tests
scripts/            Docker startup and explicitly authorized smoke helpers
Dockerfile          Non-root production image definition
compose.yaml        Local/development one-command HTTP deployment
DEPLOYMENT.md       Configuration, scaling, security, and rollback contract
KNOWN_ISSUES.md     Confirmed external defects and their retest boundaries
```

The installable package contains runtime code only. Deployment and known-issue
documents remain repository documentation and are not imported by the service.

## Development and verification

Create a Python 3.11+ environment and install all development and server
dependencies:

```bash
python -m pip install -e '.[dev,server]'
```

The repository defines the following local checks:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest discover -s tests
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m compileall -q src tests scripts
python -m build --sdist --wheel
twine check dist/*
check-wheel-contents dist/*.whl
ruff check src tests scripts
mypy src/weex_mcp
PYTHONPATH=src python3 -m weex_mcp --smoke
PYTHONPATH=src python3 -m weex_mcp serve --help
```

Unit tests use injected transports and adapters. Network-dependent demo smoke
execution requires explicit authorization and is not part of the default test
suite.

Generated `build/`, `dist/`, `*.egg-info/`, virtual environments, `.env`, and
local logs are ignored. Do not commit real service secrets or WEEX credentials.

## Troubleshooting

- `module_disabled`: Trade is off by default. Enable it only in an explicitly
  approved environment, then restart the service.
- `weex-mcp serve` exits with code 2: check the required cloud service secrets,
  numeric settings, allowed origins, and HTTPS upstream origins.
- HTTP `/mcp` returns 401: supply the configured service bearer token. This is
  not a WEEX API key.
- A modern HTTP request reports `header_mismatch`: send the negotiated
  `MCP-Protocol-Version` and required modern request metadata, or perform a
  supported legacy `initialize` first.
- A preview ID becomes invalid after restart or rotation: local instances
  without an explicit approval secret generate a per-process secret; cloud
  replicas must share one configured secret. Rotation invalidates old tokens.
- A Trade result is unknown after a timeout: do not blindly retry. Reconcile by
  the stable `client_order_id` through a read-only query before any new write.
- Container readiness fails: run `docker compose config`, inspect `/readyz`, and
  confirm that the container and published port settings match.

## Known issues

Confirmed external platform defects and their safe retest conditions are
tracked in
[KNOWN_ISSUES.md](https://github.com/weex-labs/weex-mcp/blob/main/KNOWN_ISSUES.md).
These records do not constitute
authorization to submit new trade requests.

## Operational boundary

The package provides MCP protocol handling, strict configuration validation,
request-scoped credential resolution, WEEX REST adapters, health endpoints,
and a container entry point. External infrastructure remains responsible for
TLS termination, secret distribution, gateway policy, request-body log
suppression, monitoring, alerting, rate limiting, deployment rollout, and
reconciliation of unknown trade outcomes. The service intentionally does not
implement built-in rate limiting.

## License

This project is licensed under the MIT License. See
[LICENSE](https://github.com/weex-labs/weex-mcp/blob/main/LICENSE).
