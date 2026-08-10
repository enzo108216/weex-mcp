# WEEX MCP deployment

This package supports two runtime modes:

- Local mode: run `weex-mcp stdio`, or import `build_default_service` /
  `build_asgi_app` in a Python host. It keeps the lightweight core
  dependency-free and preserves the existing local defaults.
- Cloud mode: install the `server` extra and start `weex-mcp serve`. Cloud mode
  validates required shared secrets before opening a listening socket.

Neither mode stores API credentials, preview state, idempotency results, or
user sessions. Account and Trade credentials are sent in the individual MCP
request body and used only for that request.

## Required cloud configuration

| Environment variable | Required | Default | Meaning |
| --- | --- | --- | --- |
| `WEEX_APPROVAL_TOKEN_SECRET` | Cloud: yes | Local: generated per service instance | At least 32 UTF-8 bytes. Every replica must use the same value so a confirm can verify a token issued by another replica. |
| `WEEX_SERVICE_AUTH_TOKEN` | Cloud: yes | Local: empty | Bearer token required by `/mcp`; printable ASCII only, without whitespace. Compared in constant time. |
| `WEEX_MCP_PORT` | No | `8080` | Default `weex-mcp serve` and container health-check port; an explicit `--port` wins for the process. |
| `WEEX_TRADE_ENABLED` | No | `false` | Strict `true`/`false`. Keep false until Trade write access is intentionally enabled. |
| `WEEX_APPROVAL_TOKEN_TTL_SECONDS` | No | `120` | Positive approval-token lifetime in seconds. |
| `WEEX_ALLOWED_ORIGINS` | No | empty | Comma-separated exact HTTP(S) origins. Wildcards, credentials, paths, query strings, and fragments are rejected. Requests without an `Origin` header remain valid for non-browser MCP clients. |
| `WEEX_MAX_REQUEST_BYTES` | No | `1048576` | Positive maximum request-body size. |
| `WEEX_MARKET_TIMEOUT_SECONDS` | No | `5` | Positive finite public Market upstream timeout. |
| `WEEX_ACCOUNT_TIMEOUT_SECONDS` | No | `5` | Positive finite Account/Trade upstream timeout. |
| `WEEX_SERVER_NAME` | No | `weex-mcp` | Non-empty service name exposed by health endpoints. |

Do not put either required secret in an image, repository, URL, or command-line
argument. Inject them through the deployment platform's secret mechanism.
`Settings.__repr__` excludes their values, but that does not protect process
environment dumps or third-party instrumentation.

## Run as a process

```bash
python -m pip install '.[server]'
export WEEX_APPROVAL_TOKEN_SECRET='replace-with-at-least-32-random-bytes'
export WEEX_SERVICE_AUTH_TOKEN='replace-with-a-random-service-token'
export WEEX_TRADE_ENABLED='false'
export WEEX_MCP_PORT='8080'
weex-mcp serve --host 0.0.0.0 --workers 1
```

`GET /healthz` is a liveness check. `GET /readyz` is a readiness check and is
available without the service bearer token; neither response contains secrets.
Cloud configuration is validated before Uvicorn starts, so a missing or invalid
required value terminates the command with exit code 2.

## Run the container

```bash
docker build -t weex-mcp:local .
docker run --rm -p 8080:8080 \
  -e WEEX_APPROVAL_TOKEN_SECRET='replace-with-at-least-32-random-bytes' \
  -e WEEX_SERVICE_AUTH_TOKEN='replace-with-a-random-service-token' \
  -e WEEX_TRADE_ENABLED=false \
  weex-mcp:local
```

The image runs as an unprivileged numeric user. Its health check calls
`/readyz` on `WEEX_MCP_PORT`. To use port 9090, publish `9090:9090` and set
`-e WEEX_MCP_PORT=9090`; the server and health check then use the same port.

## MCP request boundary

`POST /mcp` always requires cloud service authorization and allows two protocol
eras:

- `Authorization: Bearer <WEEX_SERVICE_AUTH_TOKEN>` in cloud mode;
- Codex/Claude legacy: `initialize` negotiates `2025-06-18` or `2025-11-25`
  from the request body and may omit the version header; later requests send
  that version in `MCP-Protocol-Version`;
- modern `2026-07-28`: the configured protocol header, matching `_meta`,
  `Mcp-Method`, and `Mcp-Name` for named calls are mandatory;
- a plaintext credential object in each Account/Trade tool call when that tool
  needs WEEX authentication.

Legacy requests do not require or receive 2026-only `resultType`, `cacheScope`,
or `ttlMs` fields. A modern request missing `MCP-Protocol-Version` fails with
JSON-RPC `-32020 HeaderMismatch`. No protocol choice is saved across HTTP
requests, and the server does not issue a user session.

TLS must terminate before the request reaches an untrusted network. Disable
request-body capture in reverse proxies, APM agents, tracing middleware, error
reporters, and access-log customizations because the body can contain
`api_key`, `api_secret`, and `api_passphrase`. The built-in Uvicorn access log
does not log bodies, but surrounding infrastructure must be checked separately.

## Scaling, rotation, and retry boundary

The service is stateless and can run multiple replicas without Redis or sticky
sessions. All replicas must have the same approval-token secret and compatible
package version. A token is replayable until expiry; use a stable
`client_order_id`, and do not describe this behavior as exactly-once.

Synchronous WEEX REST adapters are dispatched outside the ASGI event loop.
Size worker/thread capacity and upstream timeouts for expected concurrency;
this prevents one slow upstream call from blocking unrelated event-loop work
but is not an unbounded workload queue.

This version accepts one approval-token secret at a time. Rotating it
immediately invalidates outstanding tokens. Coordinate the switch across all
replicas, or drain preview traffic and wait at least the configured TTL before
rotation. A rolling deployment with mixed signing secrets is not supported.

If a Trade confirm times out after the exchange may have accepted it, do not
blindly resubmit with a new client order ID. Query the exchange using the stable
ID and reconcile the unknown outcome in the hosting system. That compensation,
along with TLS, gateway policy, monitoring, alerting, and platform rollout, is
an external deployment responsibility.

## Rollback

Roll back the image/wheel and its clients as one compatible version. Confirm
schemas changed from the historical stateful `preview_id` design to complete
parameters plus `approval_token`; do not mix clients and servers from those two
contracts. No cache or database cleanup is required because the MCP stores no
preview, idempotency, credential, or user state.
