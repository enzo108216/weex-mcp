from __future__ import annotations

from .adapters.account import AccountAdapter
from .adapters.market import MarketPriceAdapter
from .adapters.partner import PartnerAdapter
from .adapters.trade_guard import TradeGuardAdapter
from .audit import AuditSink
from .execution_authorization import ExecutionAuthorizer
from .request_description import RequestDescriptionError, describe_request
from .secret_provider import SecretProvider
from .service import McpService, _route_approval_binding, build_default_service
from .settings import Settings
from .tool_registry import build_default_registry


def build_local_user_service(
    *,
    settings: Settings,
    secret_provider: SecretProvider,
    market_adapter: MarketPriceAdapter | None = None,
    account_adapter: AccountAdapter | None = None,
    partner_adapter: PartnerAdapter | None = None,
    trade_adapter: TradeGuardAdapter | None = None,
    execution_authorizer: ExecutionAuthorizer | None = None,
    audit_sink: AuditSink | None = None,
) -> McpService:
    """Build the profile-bound Local User service without Broker-only tools."""

    registry = build_default_registry(
        enabled_modules={"market", "account", "partner", "trade"},
        credential_modes={"credential_ref"},
        allow_delegated_authorization=True,
    )
    return build_default_service(
        settings=settings,
        runtime_profile="local_user",
        registry=registry,
        approval_binding=_route_approval_binding(host_kind="local_user", settings=settings),
        market_adapter=market_adapter,
        account_adapter=account_adapter,
        partner_adapter=partner_adapter,
        secret_provider=secret_provider,
        trade_adapter=trade_adapter,
        execution_authorizer=execution_authorizer,
        audit_sink=audit_sink,
    )


__all__ = [
    "RequestDescriptionError",
    "build_local_user_service",
    "describe_request",
]
