from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from .audit import make_fingerprint
from .credential_context import CredentialContext, CredentialError
from .tenant_context import TenantContext


class SecretProviderError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

    def safe_message(self) -> str:
        messages = {
            "secret_provider_unconfigured": "Secret provider is not configured for private WEEX API calls.",
            "credential_invalid": "Credential provider rejected the credential context.",
            "credential_ref_not_found": "Credential reference was not found.",
            "permission_denied": "Credential does not allow the required permission.",
        }
        return messages.get(self.code, "Credential provider failed to resolve credentials.")


@dataclass(frozen=True)
class ResolvedWeexCredential:
    key_id: str
    api_key: str
    api_secret: str
    api_passphrase: str
    permissions: frozenset[str]
    fingerprint: str

    def safe_audit_dict(self) -> dict[str, Any]:
        return {
            "credential_fingerprint": self.fingerprint,
            "permissions": sorted(self.permissions),
        }


@runtime_checkable
class SecretProvider(Protocol):
    def resolve(
        self,
        *,
        credential_argument: dict[str, Any],
        context: TenantContext,
        required_permission: str,
    ) -> ResolvedWeexCredential:
        ...


class UnconfiguredSecretProvider:
    """Fail-closed provider used until platform secret exchange is configured."""

    def resolve(
        self,
        *,
        credential_argument: dict[str, Any],
        context: TenantContext,
        required_permission: str,
    ) -> ResolvedWeexCredential:
        raise SecretProviderError(
            "secret_provider_unconfigured",
            "Secret provider is not configured for private WEEX API calls.",
        )


class PlaintextSecretProvider:
    """Resolve a complete WEEX credential supplied with the current request."""

    def resolve(
        self,
        *,
        credential_argument: dict[str, Any],
        context: TenantContext,
        required_permission: str,
    ) -> ResolvedWeexCredential:
        try:
            context_credential = CredentialContext.from_argument(credential_argument)
        except CredentialError as exc:
            raise SecretProviderError(exc.code, exc.message) from exc

        if context_credential.kind != "plaintext":
            raise SecretProviderError("credential_invalid", "PlaintextSecretProvider only supports plaintext credentials.")

        declared_permissions = context_credential.permissions
        if declared_permissions and required_permission not in declared_permissions:
            raise SecretProviderError("permission_denied", f"Credential does not allow {required_permission}.")

        api_key = str(credential_argument["api_key"])
        api_secret = str(credential_argument["api_secret"])
        api_passphrase = str(credential_argument["api_passphrase"])
        return ResolvedWeexCredential(
            key_id="inline",
            api_key=api_key,
            api_secret=api_secret,
            api_passphrase=api_passphrase,
            # Request metadata may narrow a call but cannot prove what WEEX
            # granted to an inline API key. The signed endpoint adapter adds
            # verified permissions after authenticating the credential.
            permissions=frozenset(),
            fingerprint=make_fingerprint("resolved_weex_api_key", api_key),
        )


class RequestSecretProvider:
    """Default provider for inline credentials, while refs remain fail-closed."""

    def __init__(self) -> None:
        self._plaintext = PlaintextSecretProvider()
        self._unconfigured = UnconfiguredSecretProvider()

    def resolve(
        self,
        *,
        credential_argument: dict[str, Any],
        context: TenantContext,
        required_permission: str,
    ) -> ResolvedWeexCredential:
        if credential_argument.get("type") == "plaintext":
            return self._plaintext.resolve(
                credential_argument=credential_argument,
                context=context,
                required_permission=required_permission,
            )
        return self._unconfigured.resolve(
            credential_argument=credential_argument,
            context=context,
            required_permission=required_permission,
        )


class StaticMappingSecretProvider:
    """Test/development provider backed by an explicit in-memory mapping.

    This is not intended for production persistence; production should inject a
    provider that exchanges short-lived credential refs through the platform
    secret service.
    """

    def __init__(self, credentials_by_ref: dict[str, dict[str, Any]]):
        self._credentials_by_ref = credentials_by_ref

    def resolve(
        self,
        *,
        credential_argument: dict[str, Any],
        context: TenantContext,
        required_permission: str,
    ) -> ResolvedWeexCredential:
        try:
            context_credential = CredentialContext.from_argument(credential_argument)
        except CredentialError as exc:
            raise SecretProviderError(exc.code, exc.message) from exc

        if context_credential.kind != "credential_ref":
            raise SecretProviderError("credential_invalid", "StaticMappingSecretProvider only supports credential_ref.")
        ref = credential_argument.get("ref")
        record = self._credentials_by_ref.get(str(ref))
        if record is None:
            raise SecretProviderError("credential_ref_not_found", "Credential reference was not found.")

        permissions = frozenset(str(item) for item in record.get("permissions", []) if item)
        if required_permission not in permissions:
            raise SecretProviderError("permission_denied", f"Credential does not allow {required_permission}.")

        api_key = str(record.get("api_key") or "")
        api_secret = str(record.get("api_secret") or "")
        api_passphrase = str(record.get("api_passphrase") or "")
        if not api_key or not api_secret or not api_passphrase:
            raise SecretProviderError("credential_invalid", "Resolved credential is incomplete.")

        return ResolvedWeexCredential(
            key_id=context_credential.key_id,
            api_key=api_key,
            api_secret=api_secret,
            api_passphrase=api_passphrase,
            permissions=permissions,
            fingerprint=make_fingerprint("resolved_weex_api_key", context_credential.key_id, api_key),
        )
