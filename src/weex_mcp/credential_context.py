from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .audit import make_fingerprint


class CredentialError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def reject_secret_in_query(query_params: dict[str, Any]) -> None:
    # The MCP endpoint has no query-level contract.  Rejecting the whole query
    # string prevents credentials from bypassing a key-name deny-list (for
    # example ``?token=...`` or ``?key=...``) and keeps secrets out of access
    # logs and intermediary URLs.
    if query_params:
        raise CredentialError("credential_in_query", "Credential material is not allowed in URL query parameters.")


@dataclass(frozen=True)
class CredentialContext:
    kind: str
    key_id: str
    fingerprint: str
    permissions: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def from_argument(cls, raw: Any) -> CredentialContext:
        if raw is None:
            raise CredentialError("credential_missing", "Credential context is required for this tool.")
        if not isinstance(raw, dict):
            raise CredentialError("credential_invalid", "Credential context must be an object.")

        kind = raw.get("type")
        permissions = frozenset(str(item) for item in raw.get("permissions", []) if item)
        if not kind or not isinstance(kind, str):
            raise CredentialError("credential_invalid", "Credential type is required.")

        if kind == "plaintext":
            for required in ("api_key", "api_secret", "api_passphrase"):
                value = raw.get(required)
                if not isinstance(value, str) or not value:
                    raise CredentialError("credential_invalid", f"plaintext.{required} is required.")
            api_key = str(raw["api_key"])
            fingerprint = make_fingerprint("resolved_weex_api_key", api_key)
            return cls(kind=kind, key_id="inline", fingerprint=fingerprint, permissions=permissions)

        key_id = raw.get("key_id")
        if not key_id or not isinstance(key_id, str):
            raise CredentialError("credential_invalid", "Credential key_id is required.")

        if kind == "credential_ref":
            ref = raw.get("ref")
            if not ref or not isinstance(ref, str):
                raise CredentialError("credential_invalid", "credential_ref.ref is required.")
            fingerprint = make_fingerprint(kind, key_id, ref)
            return cls(kind=kind, key_id=key_id, fingerprint=fingerprint, permissions=permissions)

        if kind == "encrypted_envelope":
            for required in ("ciphertext", "nonce", "alg"):
                if not raw.get(required):
                    raise CredentialError("credential_invalid", f"encrypted_envelope.{required} is required.")
            fingerprint = make_fingerprint(kind, key_id, str(raw["ciphertext"]))
            return cls(kind=kind, key_id=key_id, fingerprint=fingerprint, permissions=permissions)

        raise CredentialError("credential_invalid", f"Unsupported credential type: {kind}")

    def has_permission(self, permission: str) -> bool:
        return permission in self.permissions

    def require_permission(self, permission: str) -> None:
        if not self.has_permission(permission):
            raise CredentialError("permission_denied", f"Credential does not allow {permission}.")

    def require_permission_if_declared(self, permission: str) -> None:
        """Reject an explicitly insufficient declaration, otherwise defer to the provider.

        A credential reference normally carries no permission list in the MCP
        request. The injected SecretProvider is the source of truth for the
        resolved WEEX key's permissions; request metadata can only provide an
        early, fail-closed rejection when it is present.
        """
        if self.permissions:
            self.require_permission(permission)

    def safe_audit_dict(self) -> dict[str, Any]:
        return {
            "credential_type": self.kind,
            "credential_fingerprint": self.fingerprint,
            "permissions": sorted(self.permissions),
        }
