# WEEX MCP deployment

Transport and runtime profile are separate choices:

- `default`: used by `weex-mcp stdio` and `build_default_service`. It exposes
  26 tools with Trade off or 52 with Trade on and advertises all core
  credential schema modes for an embedding host.
- `local_user`: built by `build_local_user_service` for a profile-bound local
  host. It accepts only `credential_ref`, exposes Local-only Partner reads,
  excludes Broker OAuth, and has 31/57 tools with Trade off/on.
- `broker_server`: used by `weex-mcp serve` / `build_cloud_app`. It accepts
  request-scoped `plaintext`, includes Broker OAuth, excludes Partner and
  delegated authorization, and has 26/52 tools with Trade off/on.

No runtime profile stores API credentials, OAuth state, preview state, idempotency
results, or user sessions. Credential refs or plaintext Account, Partner, and
Trade credentials and Broker OAuth client values are supplied per request and
used only for that request.

## Required cloud configuration

| Environment variable | Required | Default | Meaning |
| --- | --- | --- | --- |
| `WEEX_APPROVAL_TOKEN_SECRET` | Cloud: yes | Local: generated per service instance | At least 32 UTF-8 bytes. Every replica must use the same value so a confirm can verify a token issued by another replica. |
| `WEEX_SERVICE_AUTH_TOKEN` | Cloud: yes | Local: empty | Bearer token required by `/mcp`; at least 32 printable ASCII characters without whitespace. Compared in constant time. |
| `WEEX_MCP_BIND_HOST` | No | `127.0.0.1` | Process or Compose host bind address. Use `0.0.0.0` for all IPv4 interfaces or a specific host interface IP for narrower exposure. |
| `WEEX_MCP_PORT` | No | `8080` | Default `weex-mcp serve` and container health-check port; an explicit `--port` wins for the process. |
| `WEEX_MCP_CONTAINER_UID` / `WEEX_MCP_CONTAINER_GID` | Compose: no | `65532` / `65532` | Container runtime user/group. `scripts/docker-start.sh` writes the host UID/GID on first run so the mounted audit directory is writable. |
| `WEEX_TRADE_ENABLED` | No | `false` | Strict `true`/`false`. Keep false until Trade write access is intentionally enabled. |
| `WEEX_APPROVAL_TOKEN_TTL_SECONDS` | No | `300` | Positive approval-token lifetime in seconds. |
| `WEEX_ALLOWED_ORIGINS` | No | empty | Comma-separated exact HTTP(S) origins. Wildcards, credentials, paths, query strings, and fragments are rejected. Requests without an `Origin` header remain valid for non-browser MCP clients. |
| `WEEX_MAX_REQUEST_BYTES` | No | `1048576` | Positive maximum request-body size. |
| `WEEX_MARKET_TIMEOUT_SECONDS` | No | `5` | Positive finite public Market upstream timeout. |
| `WEEX_MARKET_MAX_ATTEMPTS` | No | `3` | Positive attempt count for idempotent public Market GET requests. |
| `WEEX_MARKET_RETRY_BASE_DELAY_SECONDS` | No | `0.1` | Non-negative finite base delay for exponential Market GET retry backoff. |
| `WEEX_ACCOUNT_TIMEOUT_SECONDS` | No | `5` | Positive finite Account/Trade/Broker upstream timeout. |
| `WEEX_SPOT_BASE_URL` | No | `https://api-spot.weex.com` | Deployment-selected Spot HTTPS origin. Paths, credentials, query strings, and fragments are rejected. |
| `WEEX_FUTURES_BASE_URL` | No | `https://api-contract.weex.com` | Deployment-selected Futures HTTPS origin with the same origin-only validation. |
| `WEEX_SERVER_NAME` | No | `weex-mcp` | Non-empty service name exposed by health endpoints. |
| `WEEX_AUDIT_LOG_FILE` | No | empty; Compose default: `/var/log/weex-mcp/weex_mcp_audit.log` | Optional local JSONL audit file for redacted Account/Broker/Partner/Trade tool events. |

Do not put either required secret in an image, repository, URL, or command-line
argument. Inject them through the deployment platform's secret mechanism.
`Settings.__repr__` excludes their values, but that does not protect process
environment dumps or third-party instrumentation.

## Run as a process

```bash
python -m pip install 'weex-mcp[server]'
export WEEX_APPROVAL_TOKEN_SECRET='replace-with-at-least-32-random-bytes'
export WEEX_SERVICE_AUTH_TOKEN='replace-with-at-least-32-random-ascii-characters'
export WEEX_TRADE_ENABLED='false'
export WEEX_MCP_PORT='8080'
weex-mcp serve --workers 1
```

`GET /healthz` is a liveness check. `GET /readyz` is a readiness check and is
available without the service bearer token; neither response contains secrets.
Cloud configuration is validated before Uvicorn starts, so a missing or invalid
required value terminates the command with exit code 2.

The process defaults to `127.0.0.1`. Use `--host 0.0.0.0` to listen on all
IPv4 interfaces, or `--host 192.168.1.20` to bind one host interface. The same
choice can be supplied through `WEEX_MCP_BIND_HOST`; an explicit CLI flag wins.

## Run the container

```bash
docker build -t weex-mcp:local .
docker run --rm -p 127.0.0.1:8080:8080 \
  -e WEEX_APPROVAL_TOKEN_SECRET='replace-with-at-least-32-random-bytes' \
  -e WEEX_SERVICE_AUTH_TOKEN='replace-with-at-least-32-random-ascii-characters' \
  -e WEEX_TRADE_ENABLED=false \
  weex-mcp:local
```

The image runs as an unprivileged numeric user. Its health check calls
`/readyz` on `WEEX_MCP_PORT`. To use port 9090, publish `9090:9090` and set
`-e WEEX_MCP_PORT=9090`; the server and health check then use the same port.

### One-command Compose startup

For a local or development deployment, Docker Compose can generate the
service-only secrets and wait for readiness:

```bash
./scripts/docker-start.sh
```

The first run creates `.env` with mode `0600`, random
`WEEX_APPROVAL_TOKEN_SECRET` and `WEEX_SERVICE_AUTH_TOKEN`, and
`WEEX_TRADE_ENABLED=false`. Existing `.env` content is never overwritten.
The script runs `docker compose up -d --build --wait`; it does not generate,
read, or persist WEEX user API keys. Set `WEEX_MCP_PORT` for the container
listen port and `WEEX_MCP_HOST_PORT` for the host-published port before first
run. The published port binds to `WEEX_MCP_BIND_HOST=127.0.0.1` by default;
set it to `0.0.0.0` for all host IPv4 interfaces or to a specific host
interface IP for narrower exposure. The first-run value is saved in `.env`;
existing files remain unchanged. Stop the service with `docker compose down`.

For example:

```bash
WEEX_MCP_BIND_HOST=0.0.0.0 ./scripts/docker-start.sh
WEEX_MCP_BIND_HOST=192.168.1.20 ./scripts/docker-start.sh
```

Remote clients use a reachable DNS name or host IP, not `0.0.0.0`, and still
must provide the configured Bearer token. External exposure requires TLS,
firewall or network policy, request-body log suppression, monitoring, and
appropriate gateway traffic controls.

For explicit configuration, copy `.env.example` to `.env` and run:

```bash
docker compose up -d --build --wait
```

Fill both required service secret values first. They are intentionally empty
in `.env.example`, so an unchanged example fails closed instead of starting
with shared placeholder credentials.

To target an approved staging environment, set both upstream origins in the
deployment environment or env file. They apply together to Market, Account,
and Trade adapters and cannot be overridden by an MCP tool request. Local User
Partner calls additionally accept only the official production Spot origin or
a non-bare HTTPS subdomain of `weex.tech`, use a fixed 30-second timeout, and
never retry automatically. Keep Trade
disabled until the chosen environment, account permissions, and cleanup plan
have been verified.

This Compose helper is a local/development convenience. Production still
requires platform-managed secrets, TLS termination, request-body log
suppression, rate limiting, and independent rollout/observation controls.

## MCP request boundary

`POST /mcp` always requires cloud service authorization and allows two protocol
eras:

- `Authorization: Bearer <WEEX_SERVICE_AUTH_TOKEN>` in cloud mode;
- Codex/Claude legacy: `initialize` negotiates `2025-06-18` or `2025-11-25`
  from the request body and may omit the version header; later requests send
  that version in `MCP-Protocol-Version`;
- modern `2026-07-28`: the configured protocol header, matching `_meta`,
  `Mcp-Method`, and `Mcp-Name` for named calls are mandatory;
- a plaintext credential object in each Broker Server Account or Trade tool
  call when that tool needs WEEX authentication;
- per-request Broker OAuth values for the two Broker tools. The service does
  not bind or persist merchant clients, state, tokens, or generated API keys.

When Trade is enabled, every valid preview and confirm performs a signed
read-only permission probe before an approval token can be issued or a write
request can run. Local required-field, market, and action validation happens
before this probe. Spot probes `GET /api/v3/account`; futures live/demo probe
`GET /capi/v3/account/accountConfig`. The response must authenticate and contain
the boolean `canTrade=true`. This adds one private GET to preview and one to
confirm; readiness never performs this probe.

Legacy requests do not require or receive 2026-only `resultType`, `cacheScope`,
or `ttlMs` fields. A modern request missing `MCP-Protocol-Version` fails with
JSON-RPC `-32020 HeaderMismatch`. No protocol choice is saved across HTTP
requests, and the server does not issue a user session.

TLS must terminate before the request reaches an untrusted network. Disable
request and response body capture in reverse proxies, APM agents, tracing
middleware, error reporters, and access-log customizations. Requests can
contain `client_secret`, authorization codes, PKCE verifiers, API keys, secrets,
and passphrases; successful Broker responses intentionally contain a verifier
or newly created API credential. The built-in Uvicorn access log does not log
bodies, but surrounding infrastructure must be checked separately.

If `WEEX_AUDIT_LOG_FILE` is set, the service appends one redacted JSON object
per Account/Broker/Partner/Trade tool call to that file. The default Compose
configuration sets it to `/var/log/weex-mcp/weex_mcp_audit.log` and mounts
`./logs` from the host, so the host file is `logs/weex_mcp_audit.log`. Raw credential values are
redacted before writing; retain, rotate, and protect the mounted `logs`
directory according to the deployment's audit policy.

## Compatibility preflight

Read `_meta["com.weex/mcp"].compatibility` from `server/discover` before a host
starts business requests. Validate the expected `runtime_profile`, package and
tool-contract versions, exact tool requirements, credential modes, and
delegated-authorization flag. `tool_set_digest` binds sorted tool names,
`schema_digest` binds their input/output schemas, and `manifest_digest` binds
the whole compatibility manifest. The manifest is derived from the effective
Trade-on/off catalog and never contains upstream origins, service secrets,
credential values, or credential refs.

Do not treat a matching package version alone as compatibility evidence. A
tool or schema mismatch fails closed; switch the package and its client as one
versioned unit instead of falling back to request-level direct REST.

## Scaling, rotation, and retry boundary

The service is stateless and can run multiple replicas without Redis or sticky
sessions. All replicas must have the same approval-token secret and compatible
package version. A token is replayable until expiry; use a stable
`client_order_id`, and do not describe this behavior as exactly-once.

Synchronous WEEX REST adapters are dispatched outside the ASGI event loop.
Size worker/thread capacity and upstream timeouts for expected concurrency;
this prevents one slow upstream call from blocking unrelated event-loop work
but is not an unbounded workload queue.

Only public, idempotent Market GET transport failures (`TimeoutError` and
non-HTTP URL transport errors) receive bounded exponential-backoff retries.
HTTP responses, WEEX business errors, invalid JSON, malformed response shapes,
Account calls, Broker token/API-key calls, Trade permission probes, and all
private/write calls are never automatically retried by the MCP. In particular,
an unknown Trade write result must be reconciled by client order ID before any
new write is considered. An unknown Broker `create-api` result must not be
retried because the API key may already exist and its secret is returned only
once.

This version accepts one approval-token secret at a time. Rotating it
immediately invalidates outstanding tokens. Coordinate the switch across all
replicas, or drain preview traffic and wait at least the configured TTL before
rotation. A rolling deployment with mixed signing secrets is not supported.

If a Trade confirm times out after the exchange may have accepted it, do not
blindly resubmit with a new client order ID. Query the exchange using the stable
ID and reconcile the unknown outcome in the hosting system. That compensation,
along with TLS, gateway policy, monitoring, alerting, rate limiting, and
platform rollout, is an external deployment responsibility. This service does
not provide built-in rate limiting.

## Release checklist

Version `0.2.0` is currently an unpublished candidate; this repository state
does not authorize a tag, PyPI upload, MCP Registry submission, or deployment.

1. Keep the single package version in `src/weex_mcp/_version.py` identical to
   both version fields in `server.json`.
2. From a fixed commit, build one candidate artifact set outside the checkout,
   record wheel/sdist hashes and every profile manifest digest, and run the
   full unit, Ruff, mypy, installed-wheel, Registry schema, Compose, and Docker
   checks used by CI.
3. Preserve that candidate artifact. After separate publication authorization,
   upload the exact artifact bytes that passed verification; do not rebuild and
   claim byte identity. Publish PyPI before submitting the matching
   `server.json` to the MCP Registry.
4. Deploy the same immutable artifact to a non-production environment, verify
   `/readyz`, authenticated MCP requests, compatibility preflight, and the
   expected Trade-disabled catalog, then promote only that exact artifact.

Do not publish a Registry version whose PyPI package is missing, and do not
reuse a version number for different code. External publication credentials
belong in the release platform, never in this repository.

## Rollback

The rollback boundary is the package plus its clients and compatibility
manifest. Roll back the image/wheel and its clients as one compatible version.
The current stateless confirmation contract resubmits complete action parameters with a
signed `preview_id`; `approval_token` is only a deprecated compatibility alias
accepted during client migration. Do not mix this contract with historical
stateful preview implementations. No cache or database cleanup is required
because the MCP stores no preview, idempotency, credential, or user state.
