from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
from collections.abc import Callable, Mapping
from typing import Any

from .trade_execution import canonical_payload_bytes


class ApprovalTokenError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class ApprovalTokenCodec:
    def __init__(
        self,
        *,
        secret: str,
        ttl_seconds: int = 300,
        time_func: Callable[[], float] | None = None,
        binding: Mapping[str, Any] | None = None,
    ):
        secret_bytes = secret.encode("utf-8")
        if len(secret_bytes) < 32:
            raise ValueError("approval_token_secret must be at least 32 bytes.")
        if ttl_seconds <= 0:
            raise ValueError("approval_token_ttl_seconds must be positive.")
        self._secret = secret_bytes
        self._ttl_seconds = ttl_seconds
        self._time_func = time_func or time.time
        self._binding = _canonical_binding(binding)
        self._version = 2 if self._binding else 1

    def issue(
        self,
        *,
        action: str,
        credential_fingerprint: str,
        summary: dict[str, Any],
        risk: dict[str, Any],
        risk_signature: str,
    ) -> str:
        issued_at = int(self._time_func())
        claims = {
            "v": self._version,
            "action": action,
            "credential_fingerprint": credential_fingerprint,
            "issued_at": issued_at,
            "expires_at": issued_at + self._ttl_seconds,
            "summary": summary,
            "risk": risk,
            "risk_signature": risk_signature,
        }
        if self._version == 2:
            claims["binding"] = self._binding
        try:
            payload = canonical_payload_bytes(claims)
        except (TypeError, ValueError, OverflowError):
            raise ApprovalTokenError(
                "approval_token_issue_failed",
                "Approval token could not be issued.",
            ) from None
        signature = hmac.new(self._secret, payload, hashlib.sha256).digest()
        return f"{_encode(payload)}.{_encode(signature)}"

    def verify(self, token: Any) -> dict[str, Any]:
        if not isinstance(token, str) or not token or token.count(".") != 1:
            raise ApprovalTokenError("approval_token_invalid", "Approval token is invalid.")
        payload_part, signature_part = token.split(".", 1)
        try:
            payload = _decode(payload_part)
            signature = _decode(signature_part)
        except (ValueError, binascii.Error):
            raise ApprovalTokenError("approval_token_invalid", "Approval token is invalid.") from None
        if _encode(payload) != payload_part or _encode(signature) != signature_part:
            raise ApprovalTokenError("approval_token_invalid", "Approval token is invalid.")
        expected = hmac.new(self._secret, payload, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ApprovalTokenError("approval_token_invalid", "Approval token is invalid.")
        try:
            claims = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ApprovalTokenError("approval_token_invalid", "Approval token is invalid.") from None
        if not _valid_claims(claims):
            raise ApprovalTokenError("approval_token_invalid", "Approval token is invalid.")
        if claims["v"] != self._version:
            raise ApprovalTokenError("approval_token_invalid", "Approval token is invalid.")
        if self._version == 2 and claims["binding"] != self._binding:
            raise ApprovalTokenError("approval_token_invalid", "Approval token is invalid.")
        now = int(self._time_func())
        if now >= claims["expires_at"]:
            raise ApprovalTokenError("approval_token_expired", "Approval token has expired.")
        if claims["issued_at"] > now + 5:
            raise ApprovalTokenError("approval_token_invalid", "Approval token is invalid.")
        return claims


def _valid_claims(claims: Any) -> bool:
    if not isinstance(claims, dict) or claims.get("v") not in {1, 2}:
        return False
    if not all(isinstance(claims.get(field), str) and claims[field] for field in ("action", "credential_fingerprint", "risk_signature")):
        return False
    if not isinstance(claims.get("issued_at"), int) or isinstance(claims.get("issued_at"), bool):
        return False
    if not isinstance(claims.get("expires_at"), int) or isinstance(claims.get("expires_at"), bool):
        return False
    if claims["expires_at"] <= claims["issued_at"]:
        return False
    if claims["v"] == 1 and "binding" in claims:
        return False
    if claims["v"] == 2 and (not isinstance(claims.get("binding"), dict) or not claims["binding"]):
        return False
    return (
        isinstance(claims.get("summary"), dict)
        and isinstance(claims.get("risk"), dict)
    )


def _canonical_binding(binding: Mapping[str, Any] | None) -> dict[str, Any]:
    if binding is None:
        return {}
    if not isinstance(binding, Mapping):
        raise ValueError("approval binding must be a JSON object.")
    try:
        return json.loads(canonical_payload_bytes(dict(binding)).decode("utf-8"))
    except (TypeError, ValueError, OverflowError, UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("approval binding must be a JSON object.") from None


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    if not value or any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for character in value):
        raise ValueError("invalid base64url")
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)
