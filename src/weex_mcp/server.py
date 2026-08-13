from __future__ import annotations

from typing import Any

from .adapters.market import MarketPriceAdapter
from .service import McpService, build_default_service
from .tenant_context import TenantContext


def build_service(*, trade_enabled: bool = True, market_adapter: MarketPriceAdapter | None = None) -> McpService:
    return build_default_service(trade_enabled=trade_enabled, market_adapter=market_adapter)


def smoke_request() -> dict[str, Any]:
    service = build_default_service()
    response = service.handle_jsonrpc(
        {"jsonrpc": "2.0", "id": "smoke", "method": "tools/list", "params": {}},
        context=TenantContext(tenant_id="smoke", user_id="smoke", session_id="smoke"),
    )
    assert response is not None
    return response
