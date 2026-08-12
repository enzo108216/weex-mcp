# Known Issues

## WEEX staging futures position cannot be closed

Status: `EXTERNAL_DEFERRED`

Owner: WEEX staging account, position, and matching-service maintainers

First confirmed: 2026-08-11

### Reproduction conditions

- The affected staging account contains an existing `BTCUSDT` short position
  with quantity `0.032813`.
- There are no open spot or futures orders, and visible order-book liquidity
  exceeds the position quantity.
- Three separately authorized close attempts were performed: a market order,
  an aggressive IOC limit order, and the official reduce-only
  `/capi/v3/closePositions` endpoint.

### Evidence and impact

All three exchange orders reached the final state `CANCELED` with
`executedQty=0`; the position remained unchanged. The official endpoint also
created a `reduceOnly=true` order, which rules out the MCP order-side mapping,
limit price, missing reduce-only behavior, and visible liquidity as the primary
causes supported by current evidence.

This issue blocks a clean staging write-path acceptance run. It does not affect
the local 47-tool contract, package build, protocol compatibility, or container
runtime verification. Staging trade execution must not be reported as passed.

On 2026-08-12, the typed `close_positions` path succeeded against a separate,
clean STG account after opening one minimum `0.0001 BTC` position. That result
confirms the MCP path and response validator, but it does not repair or clear
the affected historical position described here.

The current MCP now exposes the typed `trade_preview_close_positions` and
`trade_confirm_close_positions` pair. That capability does not resolve this
external defect: the earlier direct call to the official endpoint produced the
same canceled, zero-fill result. The typed tool must not be used for a fourth
write attempt against the affected position.

### Temporary handling

- Do not submit another write request against the affected position.
- Do not increase order size or add a new MCP write tool to work around the
  exchange state.
- Limit further activity to non-mutating status queries until the external
  owner resolves the position or provides a clean account.

### Retest conditions

Retest only after either:

1. the staging owner clears or repairs the affected position and identifies the
   remediation scope; or
2. a separate staging account is available with no spot or futures open orders
   and no futures positions.

Before any retest, confirm the clean state and current symbol, margin, and
leverage rules through read-only requests. Every new write test requires
separate authorization, explicit value limits, a recovery plan, and post-write
reconciliation. This record is not trading authorization.

## STG futures batch order rejects an otherwise valid one-item request

Status: `RESOLVED_VERIFIED`

Owner: WEEX staging Futures transaction API maintainers

First confirmed: 2026-08-12

Two controlled one-item requests to `POST /capi/v3/batchOrders` were rejected
with business code `-3236`. The account was authenticated and trade-enabled;
the same symbol, side, position side, quantity, price, time-in-force, and client
ID shape succeeded through `POST /capi/v3/order`. No batch order was created,
and read-back showed zero open orders and zero positions.

The local endpoint mapping, five-item maximum, per-item schema, response
validation, and stateless preview/confirm flow remain covered by automated
tests. A third write attempt was not made. Retest after the STG owner documents
`-3236`, confirms the account/endpoint entitlement, or supplies a known-good
batch example that remains within the approved aggregate risk boundary.

On 2026-08-12, the original project was retested through Docker against STG
with the 10 USDT bounded Trade confirm plan. A different supplied credential
successfully completed one-item Futures batch order and batch cancel through
MCP preview/confirm. The created order `782433420406948762` was canceled by
batch cancel, and final read-back showed zero open normal orders, zero open
conditional orders, and zero `ETHUSDT` positions. The earlier `-3236` behavior
is therefore not tracked as an active unresolved MCP issue.

## STG isolated-margin decrease returns business code -1054

Status: `EXTERNAL_DEFERRED`

Owner: WEEX staging Futures account/risk API maintainers

First confirmed: 2026-08-12

Increasing isolated margin by `0.01` through
`POST /capi/v3/account/positionMargin` succeeded and was confirmed by position
read-back. Decreasing the same position by `0.01` immediately afterward was
explicitly rejected with business code `-1054`. No retry or alternate amount
was submitted. The position was then closed successfully, so no margin,
position, order, or configuration residue remains.

The MCP correctly surfaced the rejection. Retest only after the STG owner
documents the code and minimum/decrease constraints for this endpoint.

## One supplied STG credential is rejected by all private endpoints

Status: `EXTERNAL_CONFIGURATION_REQUIRED`

Owner: WEEX staging API-key/account administrator

First confirmed: 2026-08-12

One supplied STG credential received HTTP 401 from Spot and Futures account
configuration, open-order, position, and symbol-configuration reads. A second
credential succeeded through the same signing implementation and origins,
including the full read-only matrix and controlled writes. This isolates the
failure to the key, account binding, permission, or environment registration;
the credential value is intentionally not recorded here.

Retest after the STG administrator confirms that the key belongs to these two
origins, is enabled, and has USER_DATA/TRADE permissions.

## STG Spot minimum-order write verification

Status: `RESOLVED_VERIFIED`

STG Spot currently reports a minimum `BTCUSDT` quantity of `0.0001 BTC`, whose
notional value during this run was above `6.36 USDT`. The original
`5.20 USDT` verification limit was an authorization boundary rather than an
MCP or exchange defect.

On 2026-08-12, the user raised the designated STG Spot limit to `7.00 USDT`.
Three serial, stateless preview/confirm scenarios then used
`SELL 0.0001 BTC @ 69999.99 USDT` (`6.999999 USDT`): ordinary order and
single cancel, a one-item batch order and batch cancel, and an ordinary seed
order followed by symbol-scope cancel. Every tested order reached
`CANCELED` with zero executed quantity. Final read-back showed zero open
Spot orders and no new trades.

The earlier verification limit is therefore resolved. This record remains as
historical evidence of the authorization boundary; it does not authorize
future STG or production writes.
