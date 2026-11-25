from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from .audit import redact_secrets
from .tenant_context import TenantContext
from .trade_execution import TradeExecution


class ExecutionAuthorizationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

    def safe_message(self) -> str:
        messages = {
            "execution_authorizer_unconfigured": "Delegated execution authorization is not configured.",
            "execution_authorization_invalid": "Delegated execution authorization is invalid.",
            "execution_authorization_denied": "Delegated execution authorization was denied.",
            "execution_authorization_expired": "Delegated execution authorization has expired.",
        }
        return messages.get(self.code, "Delegated execution authorization failed.")


@dataclass(frozen=True)
class VerifiedExecutionAuthorization:
    authorization_id: str
    scope_fingerprint: str
    expires_at: int
    audit_metadata: dict[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.authorization_id, str) or not self.authorization_id:
            raise ValueError("authorization_id must be a non-empty string.")
        if not isinstance(self.scope_fingerprint, str) or not self.scope_fingerprint:
            raise ValueError("scope_fingerprint must be a non-empty string.")
        if isinstance(self.expires_at, bool) or not isinstance(self.expires_at, int) or self.expires_at <= 0:
            raise ValueError("expires_at must be a positive integer.")
        if not isinstance(self.audit_metadata, dict):
            raise ValueError("audit_metadata must be an object.")

    def safe_audit_dict(self) -> dict[str, Any]:
        return {
            "authorization_id": self.authorization_id,
            "scope_fingerprint": self.scope_fingerprint,
            "authorization_expires_at": self.expires_at,
            "authorization_metadata": redact_secrets(self.audit_metadata),
        }


@runtime_checkable
class ExecutionAuthorizer(Protocol):
    def authorize(
        self,
        *,
        context: TenantContext,
        credential_fingerprint: str,
        execution: TradeExecution,
        delegated_ref: dict[str, Any],
    ) -> VerifiedExecutionAuthorization:
        ...


class FailClosedExecutionAuthorizer:
    def authorize(
        self,
        *,
        context: TenantContext,
        credential_fingerprint: str,
        execution: TradeExecution,
        delegated_ref: dict[str, Any],
    ) -> VerifiedExecutionAuthorization:
        raise ExecutionAuthorizationError(
            "execution_authorizer_unconfigured",
            "Delegated execution authorization is not configured.",
        )


__all__ = [
    "ExecutionAuthorizationError",
    "ExecutionAuthorizer",
    "FailClosedExecutionAuthorizer",
    "VerifiedExecutionAuthorization",
]
