from __future__ import annotations

import importlib
from collections.abc import Mapping
from typing import Any

from .asgi_app import build_asgi_app
from .service import build_default_service
from .settings import Settings


def build_cloud_app(
    environ: Mapping[str, str] | None = None,
) -> Any:
    """Build the stateless cloud ASGI application from validated environment settings."""

    settings = Settings.from_environment(environ, require_cloud=True)
    service = build_default_service(settings=settings)
    return build_asgi_app(service=service)


def create_cloud_app() -> Any:
    """Uvicorn application factory using the current process environment."""

    return build_cloud_app()


def run_cloud_server(*, host: str = "0.0.0.0", port: int = 8080, workers: int = 1) -> None:
    """Validate cloud config and start Uvicorn via its import-string factory."""

    if not host:
        raise ValueError("host must not be empty.")
    if not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535.")
    if workers <= 0:
        raise ValueError("workers must be positive.")

    # Validate before importing or starting the server so invalid configuration
    # terminates immediately without creating workers with inconsistent secrets.
    Settings.from_environment(require_cloud=True)
    try:
        uvicorn = importlib.import_module("uvicorn")
    except ModuleNotFoundError:
        raise RuntimeError("Cloud serving requires the server extra: pip install 'weex-mcp[server]'.") from None
    uvicorn.run(
        "weex_mcp.cloud:create_cloud_app",
        factory=True,
        host=host,
        port=port,
        workers=workers,
    )


__all__ = ["build_cloud_app", "create_cloud_app", "run_cloud_server"]
