from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from . import __version__


class SettingsError(ValueError):
    """Raised when runtime environment configuration is invalid."""


@dataclass(frozen=True)
class Settings:
    """Runtime settings for the MCP service core.

    Settings remain deliberately minimal for an embeddable, stateless MCP core.
    Production gateway, TLS, and secret-backend wiring remain responsibilities
    of the hosting layer.
    """

    trade_enabled: bool = False
    approval_token_secret: str = field(default="", repr=False)
    approval_token_ttl_seconds: int = 120
    server_name: str = "weex-mcp"
    server_version: str = __version__
    protocol_version: str = "2026-07-28"
    max_request_bytes: int = 1_048_576
    market_timeout_seconds: float = 5.0
    account_timeout_seconds: float = 5.0
    service_auth_token: str = field(default="", repr=False)
    allowed_origins: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.service_auth_token:
            _validate_service_auth_token(self.service_auth_token, "service_auth_token")

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        require_cloud: bool = False,
    ) -> Settings:
        """Build settings from ``WEEX_*`` environment variables.

        Embedded callers may keep the dataclass defaults. Cloud callers use
        ``require_cloud=True`` so shared signing and service-auth secrets fail
        closed before the server starts.
        """

        source = os.environ if environ is None else environ
        defaults = cls()
        approval_secret = source.get("WEEX_APPROVAL_TOKEN_SECRET", defaults.approval_token_secret)
        service_auth_token = source.get("WEEX_SERVICE_AUTH_TOKEN", defaults.service_auth_token)

        if approval_secret and len(approval_secret.encode("utf-8")) < 32:
            raise SettingsError("WEEX_APPROVAL_TOKEN_SECRET must be at least 32 bytes.")
        if require_cloud and not approval_secret:
            raise SettingsError("WEEX_APPROVAL_TOKEN_SECRET is required for cloud mode and must be at least 32 bytes.")
        if service_auth_token:
            _validate_service_auth_token(service_auth_token, "WEEX_SERVICE_AUTH_TOKEN")
        if require_cloud and not service_auth_token:
            raise SettingsError("WEEX_SERVICE_AUTH_TOKEN is required for cloud mode.")

        server_name = source.get("WEEX_SERVER_NAME", defaults.server_name).strip()
        if not server_name:
            raise SettingsError("WEEX_SERVER_NAME must not be empty.")

        return cls(
            trade_enabled=_parse_bool(source, "WEEX_TRADE_ENABLED", defaults.trade_enabled),
            approval_token_secret=approval_secret,
            approval_token_ttl_seconds=_parse_positive_int(
                source,
                "WEEX_APPROVAL_TOKEN_TTL_SECONDS",
                defaults.approval_token_ttl_seconds,
            ),
            server_name=server_name,
            server_version=defaults.server_version,
            protocol_version=defaults.protocol_version,
            max_request_bytes=_parse_positive_int(source, "WEEX_MAX_REQUEST_BYTES", defaults.max_request_bytes),
            market_timeout_seconds=_parse_positive_float(
                source,
                "WEEX_MARKET_TIMEOUT_SECONDS",
                defaults.market_timeout_seconds,
            ),
            account_timeout_seconds=_parse_positive_float(
                source,
                "WEEX_ACCOUNT_TIMEOUT_SECONDS",
                defaults.account_timeout_seconds,
            ),
            service_auth_token=service_auth_token,
            allowed_origins=_parse_allowed_origins(source.get("WEEX_ALLOWED_ORIGINS", "")),
        )


def _parse_bool(source: Mapping[str, str], name: str, default: bool) -> bool:
    raw_value = source.get(name)
    if raw_value is None:
        return default
    normalized = raw_value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise SettingsError(f"{name} must be either true or false.")


def _validate_service_auth_token(value: str, name: str) -> None:
    if any(ord(character) < 0x21 or ord(character) > 0x7E for character in value):
        raise SettingsError(f"{name} must contain only printable ASCII characters without whitespace.")


def _parse_positive_int(source: Mapping[str, str], name: str, default: int) -> int:
    raw_value = source.get(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value, 10)
    except (TypeError, ValueError):
        raise SettingsError(f"{name} must be a positive integer.") from None
    if value <= 0:
        raise SettingsError(f"{name} must be a positive integer.")
    return value


def _parse_positive_float(source: Mapping[str, str], name: str, default: float) -> float:
    raw_value = source.get(name)
    if raw_value is None:
        return default
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        raise SettingsError(f"{name} must be a positive finite number.") from None
    if not math.isfinite(value) or value <= 0:
        raise SettingsError(f"{name} must be a positive finite number.")
    return value


def _parse_allowed_origins(raw_value: str) -> tuple[str, ...]:
    origins: list[str] = []
    for origin in (item.strip() for item in raw_value.split(",")):
        if not origin:
            continue
        try:
            parsed = urlsplit(origin)
            port = parsed.port
        except ValueError:
            raise SettingsError("WEEX_ALLOWED_ORIGINS contains an invalid origin.") from None
        if (
            origin == "*"
            or parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or (port is not None and not 1 <= port <= 65535)
        ):
            raise SettingsError("WEEX_ALLOWED_ORIGINS contains an invalid origin.")
        normalized = origin[:-1] if origin.endswith("/") else origin
        if normalized not in origins:
            origins.append(normalized)
    return tuple(origins)
