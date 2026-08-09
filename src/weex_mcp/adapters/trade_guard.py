from __future__ import annotations

from typing import Any, Protocol


class TradeAdapterError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class TradeGuardAdapter(Protocol):
    def resolve_credential(self, *, credential_argument: dict[str, Any], context: Any) -> Any:
        ...

    def preview_order(
        self,
        *,
        arguments: dict[str, Any],
        credential: Any,
        credential_argument: dict[str, Any],
        context: Any,
    ) -> dict[str, Any]:
        ...

    def confirm_order(
        self,
        *,
        execution: Any,
        credential: Any,
        credential_argument: dict[str, Any],
        context: Any,
    ) -> dict[str, Any]:
        ...

    def preview_cancel_order(
        self,
        *,
        arguments: dict[str, Any],
        credential: Any,
        credential_argument: dict[str, Any],
        context: Any,
    ) -> dict[str, Any]:
        ...

    def confirm_cancel_order(
        self,
        *,
        execution: Any,
        credential: Any,
        credential_argument: dict[str, Any],
        context: Any,
    ) -> dict[str, Any]:
        ...

    def preview_leverage(
        self,
        *,
        arguments: dict[str, Any],
        credential: Any,
        credential_argument: dict[str, Any],
        context: Any,
    ) -> dict[str, Any]:
        ...

    def confirm_leverage(
        self,
        *,
        execution: Any,
        credential: Any,
        credential_argument: dict[str, Any],
        context: Any,
    ) -> dict[str, Any]:
        ...

    def preview_margin_mode(
        self,
        *,
        arguments: dict[str, Any],
        credential: Any,
        credential_argument: dict[str, Any],
        context: Any,
    ) -> dict[str, Any]:
        ...

    def confirm_margin_mode(
        self,
        *,
        execution: Any,
        credential: Any,
        credential_argument: dict[str, Any],
        context: Any,
    ) -> dict[str, Any]:
        ...


class FailClosedTradeAdapter:
    """Fail-closed adapter used when no WEEX execution adapter is configured."""

    def resolve_credential(self, **_: Any) -> Any:
        raise TradeAdapterError("adapter_not_configured", "Trade adapter is not configured.")

    def preview_order(self, **_: Any) -> dict[str, Any]:
        raise TradeAdapterError("adapter_not_configured", "Trade adapter is not configured.")

    def confirm_order(
        self,
        *,
        execution: Any,
        credential: Any,
        credential_argument: dict[str, Any],
        context: Any,
    ) -> dict[str, Any]:
        raise RuntimeError("live_trade_adapter_not_configured")

    def preview_cancel_order(self, **_: Any) -> dict[str, Any]:
        raise TradeAdapterError("adapter_not_configured", "Trade adapter is not configured.")

    def confirm_cancel_order(
        self,
        *,
        execution: Any,
        credential: Any,
        credential_argument: dict[str, Any],
        context: Any,
    ) -> dict[str, Any]:
        raise RuntimeError("cancel_order_adapter_not_configured")

    def preview_leverage(self, **_: Any) -> dict[str, Any]:
        raise TradeAdapterError("adapter_not_configured", "Trade adapter is not configured.")

    def confirm_leverage(
        self,
        *,
        execution: Any,
        credential: Any,
        credential_argument: dict[str, Any],
        context: Any,
    ) -> dict[str, Any]:
        raise RuntimeError("leverage_adapter_not_configured")

    def preview_margin_mode(self, **_: Any) -> dict[str, Any]:
        raise TradeAdapterError("adapter_not_configured", "Trade adapter is not configured.")

    def confirm_margin_mode(
        self,
        *,
        execution: Any,
        credential: Any,
        credential_argument: dict[str, Any],
        context: Any,
    ) -> dict[str, Any]:
        raise RuntimeError("margin_mode_adapter_not_configured")
