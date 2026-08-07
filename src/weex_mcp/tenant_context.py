from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

_MAX_IDENTITY_LENGTH = 256


@dataclass(frozen=True)
class TenantContext:
    tenant_id: str
    user_id: str
    session_id: str
    request_id: str = ""

    def __post_init__(self) -> None:
        for field_name in ("tenant_id", "user_id", "session_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string.")
            if len(value) > _MAX_IDENTITY_LENGTH:
                raise ValueError(f"{field_name} is too long.")
            if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
                raise ValueError(f"{field_name} contains control characters.")
        if not self.request_id:
            object.__setattr__(self, "request_id", f"req_{uuid4().hex[:16]}")

    def audit_dict(self) -> dict[str, str]:
        return {
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "request_id": self.request_id,
        }
