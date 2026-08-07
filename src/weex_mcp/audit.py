from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

_SENSITIVE_KEY_FRAGMENTS = (
    "api_key",
    "apikey",
    "secret",
    "passphrase",
    "signature",
    "ciphertext",
    "private_key",
)

_SENSITIVE_STRING_ASSIGNMENT = re.compile(
    r"(?i)(api[_-]?key|api[_-]?secret|api[_-]?passphrase|secret|passphrase|signature|ciphertext|private[_-]?key)"
    r"(\s*(?:=|:)\s*)([^\s,;&]+)"
)
_BEARER_TOKEN = re.compile(r"(?i)(\bBearer\s+)([^\s,;&]+)")


def is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(fragment in lowered for fragment in _SENSITIVE_KEY_FRAGMENTS)


def redact_secrets(value: Any) -> Any:
    """Recursively redact sensitive values before logging or returning errors."""

    if isinstance(value, Mapping):
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and is_sensitive_key(key):
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = redact_secrets(item)
        return redacted
    if isinstance(value, str):
        value = _SENSITIVE_STRING_ASSIGNMENT.sub(r"\1\2[REDACTED]", value)
        return _BEARER_TOKEN.sub(r"\1[REDACTED]", value)
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        return [redact_secrets(item) for item in value]
    return value


def make_fingerprint(*parts: str) -> str:
    digest = hashlib.sha256(":".join(parts).encode("utf-8")).hexdigest()
    return f"fp_{digest[:16]}"


@runtime_checkable
class AuditSink(Protocol):
    def record(self, event: dict[str, Any]) -> None:
        ...


class LoggingAuditSink:
    """Default local sink emitting one redacted structured log record per event."""

    def __init__(self, logger: logging.Logger | None = None):
        self._logger = logger or logging.getLogger("weex_mcp.audit")

    def record(self, event: dict[str, Any]) -> None:
        safe_event = redact_secrets(event)
        self._logger.info("weex_mcp_audit %s", json.dumps(safe_event, ensure_ascii=False, sort_keys=True))


class MemoryAuditSink:
    """Thread-safe injectable sink for tests and embedding hosts."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def record(self, event: dict[str, Any]) -> None:
        safe_event = dict(redact_secrets(event))
        with self._lock:
            self.events.append(safe_event)
