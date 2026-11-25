from __future__ import annotations

import re
from typing import Any, Protocol


class TradeAdapterError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


_OUTCOME_CLASSES = frozenset({"not_sent", "definitive_rejection", "accepted", "unknown", "partial"})
_ITEM_OUTCOME_CLASSES = frozenset({"not_sent", "definitive_rejection", "accepted", "unknown"})
_MACHINE_CODE_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_IDENTITY_PATTERN = re.compile(r"^[A-Za-z0-9_.:/-]{1,128}$")
_SAFE_DETAIL_FIELDS = frozenset(
    {
        "upstream_code",
        "http_status",
        "reason",
        "client_order_id",
        "operation_reference",
        "reconcile_identity",
    }
)


def mutation_outcome(
    *,
    outcome_class: str,
    write_attempted: bool,
    action: str,
    client_order_id: str | None = None,
    operation_reference: str | None = None,
    reconcile_identity: str | None = None,
    items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    top_level_valid = (
        outcome_class in _OUTCOME_CLASSES
        and isinstance(write_attempted, bool)
        and _outcome_attempt_is_valid(outcome_class, write_attempted)
    )
    if not isinstance(action, str) or not action:
        raise ValueError("action must be a non-empty string.")
    if not top_level_valid:
        return {"class": "unknown", "write_attempted": True, "action": action}
    outcome: dict[str, Any] = {
        "class": outcome_class,
        "write_attempted": write_attempted,
        "action": action,
    }
    for field, value in (
        ("client_order_id", client_order_id),
        ("operation_reference", operation_reference),
        ("reconcile_identity", reconcile_identity),
    ):
        if isinstance(value, str) and value:
            outcome[field] = value
    if items is not None:
        safe_items = _safe_outcome_items(items, action=action)
        if safe_items is None:
            return {"class": "unknown", "write_attempted": True, "action": action}
        aggregate = _aggregate_item_class(safe_items)
        if aggregate is None or aggregate != outcome_class:
            return {"class": "unknown", "write_attempted": True, "action": action}
        outcome["items"] = safe_items
    if outcome_class == "partial" and "items" not in outcome:
        return {"class": "unknown", "write_attempted": True, "action": action}
    return outcome


class TradeMutationError(RuntimeError):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        outcome_class: str,
        write_attempted: bool,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.code = _safe_machine_code(code)
        self.message = message if isinstance(message, str) and message else "Trade mutation failed."
        if (
            outcome_class not in {"not_sent", "definitive_rejection", "unknown"}
            or not isinstance(write_attempted, bool)
            or not _outcome_attempt_is_valid(outcome_class, write_attempted)
        ):
            self.outcome_class = "unknown"
            self.write_attempted = True
        else:
            self.outcome_class = outcome_class
            self.write_attempted = write_attempted
        self.details = _safe_mutation_details(details)

    def outcome(self, *, action: str) -> dict[str, Any]:
        return mutation_outcome(
            outcome_class=self.outcome_class,
            write_attempted=self.write_attempted,
            action=action,
            client_order_id=self.details.get("client_order_id"),
            operation_reference=self.details.get("operation_reference"),
            reconcile_identity=self.details.get("reconcile_identity"),
            items=self.details.get("items"),
        )


def _safe_mutation_details(details: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(details, dict):
        return {}
    safe: dict[str, Any] = {}
    for field in _SAFE_DETAIL_FIELDS:
        value = details.get(field)
        if field == "http_status":
            if isinstance(value, int) and not isinstance(value, bool):
                safe[field] = value
        elif field in {"upstream_code", "reason"}:
            if value not in (None, ""):
                safe[field] = _safe_machine_code(value)
        else:
            identity = safe_outcome_identity(value)
            if identity is not None:
                safe[field] = identity
    items = details.get("items")
    if isinstance(items, list):
        projected = _safe_outcome_items(items, action=None)
        if projected is not None:
            safe["items"] = projected
    return safe


def _safe_outcome_items(
    items: list[dict[str, Any]],
    *,
    action: str | None,
) -> list[dict[str, Any]] | None:
    if not items:
        return None
    safe_items: list[dict[str, Any]] = []
    seen_identities: dict[str, set[str]] = {
        "client_order_id": set(),
        "operation_reference": set(),
        "reconcile_identity": set(),
    }
    for item in items:
        if not isinstance(item, dict):
            return None
        item_class = item.get("class")
        attempted = item.get("write_attempted")
        item_action = item.get("action")
        if (
            item_class not in _ITEM_OUTCOME_CLASSES
            or not isinstance(attempted, bool)
            or not _outcome_attempt_is_valid(item_class, attempted)
            or not isinstance(item_action, str)
            or not item_action
            or (action is not None and item_action != action)
        ):
            return None
        projected: dict[str, Any] = {
            "class": item_class,
            "write_attempted": attempted,
            "action": item_action,
        }
        identity_count = 0
        for field in ("client_order_id", "operation_reference", "reconcile_identity"):
            identity = safe_outcome_identity(item.get(field))
            if identity is not None:
                if identity in seen_identities[field]:
                    return None
                seen_identities[field].add(identity)
                projected[field] = identity
                identity_count += 1
        if identity_count == 0:
            return None
        if "error_code" in item:
            projected["error_code"] = _safe_machine_code(item.get("error_code"))
        safe_items.append(projected)
    return safe_items


def _outcome_attempt_is_valid(outcome_class: str, write_attempted: bool) -> bool:
    return (outcome_class == "not_sent" and write_attempted is False) or (
        outcome_class != "not_sent" and write_attempted is True
    )


def _aggregate_item_class(items: list[dict[str, Any]]) -> str | None:
    classes = {str(item["class"]) for item in items}
    if "unknown" in classes:
        return "unknown"
    if classes == {"accepted"}:
        return "accepted"
    if classes == {"definitive_rejection"}:
        return "definitive_rejection"
    if classes == {"not_sent"}:
        return "not_sent"
    if classes == {"accepted", "definitive_rejection"}:
        return "partial"
    return None


def _safe_machine_code(value: Any) -> str:
    normalized = str(value or "").strip()
    return normalized if _MACHINE_CODE_PATTERN.fullmatch(normalized) else "upstream_error"


def safe_outcome_identity(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    normalized = str(value).strip()
    return normalized if _IDENTITY_PATTERN.fullmatch(normalized) else None


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

    def preview_mutation(
        self,
        *,
        action: str,
        arguments: dict[str, Any],
        credential: Any,
        credential_argument: dict[str, Any],
        context: Any,
    ) -> dict[str, Any]:
        ...

    def confirm_mutation(
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

    def preview_mutation(self, **_: Any) -> dict[str, Any]:
        raise TradeAdapterError("adapter_not_configured", "Trade adapter is not configured.")

    def confirm_mutation(self, **_: Any) -> dict[str, Any]:
        raise RuntimeError("trade_mutation_adapter_not_configured")
