from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import unittest
from unittest.mock import patch


async def _call_asgi(app, *, method: str, path: str, headers: list[tuple[bytes, bytes]] | None = None) -> tuple[int, bytes]:
    sent: list[dict[str, object]] = []
    events = [{"type": "http.request", "body": b"", "more_body": False}]

    async def receive() -> dict[str, object]:
        return events.pop(0)

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    await app(
        {
            "type": "http",
            "method": method,
            "path": path,
            "query_string": b"",
            "headers": headers or [],
        },
        receive,
        send,
    )
    status = next(message["status"] for message in sent if message["type"] == "http.response.start")
    body = b"".join(message.get("body", b"") for message in sent if message["type"] == "http.response.body")
    return int(status), body


class CloudRuntimeTests(unittest.TestCase):
    def _cloud_env(self) -> dict[str, str]:
        return {
            "WEEX_APPROVAL_TOKEN_SECRET": "S" * 48,
            "WEEX_SERVICE_AUTH_TOKEN": "cloud-service-token",
            "WEEX_TRADE_ENABLED": "false",
            "WEEX_APPROVAL_TOKEN_TTL_SECONDS": "90",
            "WEEX_ALLOWED_ORIGINS": "https://mcp.example.com, https://agent.example.com",
        }

    def test_cloud_environment_requires_shared_secret_and_service_auth(self):
        with self.assertRaisesRegex(ValueError, "WEEX_APPROVAL_TOKEN_SECRET"):
            from weex_mcp.settings import Settings

            Settings.from_environment({"WEEX_SERVICE_AUTH_TOKEN": "token"}, require_cloud=True)
        with self.assertRaisesRegex(ValueError, "WEEX_SERVICE_AUTH_TOKEN"):
            from weex_mcp.settings import Settings

            Settings.from_environment({"WEEX_APPROVAL_TOKEN_SECRET": "S" * 48}, require_cloud=True)

    def test_cloud_environment_parses_and_redacts_sensitive_settings(self):
        from weex_mcp.settings import Settings

        environment = self._cloud_env() | {
            "WEEX_MAX_REQUEST_BYTES": "2097152",
            "WEEX_MARKET_TIMEOUT_SECONDS": "3.5",
            "WEEX_ACCOUNT_TIMEOUT_SECONDS": "7",
        }
        settings = Settings.from_environment(environment, require_cloud=True)

        self.assertTrue(settings.trade_enabled is False)
        self.assertEqual(settings.approval_token_ttl_seconds, 90)
        self.assertEqual(settings.max_request_bytes, 2_097_152)
        self.assertEqual(settings.market_timeout_seconds, 3.5)
        self.assertEqual(settings.account_timeout_seconds, 7.0)
        self.assertEqual(settings.allowed_origins, ("https://mcp.example.com", "https://agent.example.com"))
        self.assertNotIn("S" * 48, repr(settings))
        self.assertNotIn("cloud-service-token", repr(settings))

    def test_environment_rejects_invalid_values_before_server_start(self):
        from weex_mcp.settings import Settings

        invalid_values = {
            "WEEX_TRADE_ENABLED": "sometimes",
            "WEEX_APPROVAL_TOKEN_TTL_SECONDS": "0",
            "WEEX_MAX_REQUEST_BYTES": "-1",
            "WEEX_MARKET_TIMEOUT_SECONDS": "nan",
            "WEEX_ALLOWED_ORIGINS": "*",
        }
        for name, value in invalid_values.items():
            with self.subTest(name=name):
                environment = self._cloud_env() | {name: value}
                with self.assertRaisesRegex(ValueError, name):
                    Settings.from_environment(environment, require_cloud=True)

    def test_service_bearer_rejects_non_ascii_values_for_reliable_http_comparison(self):
        from weex_mcp.settings import Settings

        with self.assertRaisesRegex(ValueError, "ASCII"):
            Settings.from_environment(
                self._cloud_env() | {"WEEX_SERVICE_AUTH_TOKEN": "cloud-tokén"},
                require_cloud=True,
            )
        with self.assertRaisesRegex(ValueError, "ASCII"):
            Settings(service_auth_token="direct-tokén")

    def test_local_environment_still_allows_embedded_defaults(self):
        from weex_mcp.service import build_default_service
        from weex_mcp.settings import Settings

        settings = Settings.from_environment({}, require_cloud=False)
        service = build_default_service(settings=settings)

        self.assertEqual(settings.service_auth_token, "")
        self.assertFalse(service.settings.trade_enabled)

    def test_cloud_app_exposes_readyz_without_exposing_secrets(self):
        from weex_mcp.cloud import build_cloud_app

        app = build_cloud_app(self._cloud_env())
        status, body = asyncio.run(_call_asgi(app, method="GET", path="/readyz"))
        payload = json.loads(body)

        self.assertEqual(status, 200)
        self.assertTrue(payload["ready"])
        self.assertNotIn("S" * 48, body.decode())
        self.assertNotIn("cloud-service-token", body.decode())

    def test_serve_cli_keeps_local_flags_and_dispatches_cloud_server(self):
        from weex_mcp.__main__ import main

        with patch.dict(os.environ, self._cloud_env(), clear=True), patch("weex_mcp.cloud.run_cloud_server") as run_server:
            result = main(["serve", "--host", "127.0.0.1", "--port", "9090", "--workers", "2"])

        self.assertEqual(result, 0)
        run_server.assert_called_once_with(host="127.0.0.1", port=9090, workers=2)

    def test_serve_cli_and_docker_healthcheck_share_the_environment_port(self):
        from pathlib import Path

        from weex_mcp.__main__ import main

        environment = self._cloud_env() | {"WEEX_MCP_PORT": "9191"}
        with patch.dict(os.environ, environment, clear=True), patch("weex_mcp.cloud.run_cloud_server") as run_server:
            result = main(["serve"])

        self.assertEqual(result, 0)
        run_server.assert_called_once_with(host="0.0.0.0", port=9191, workers=1)
        dockerfile = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text()
        self.assertIn("WEEX_MCP_PORT=8080", dockerfile)
        self.assertIn("os.environ", dockerfile)
        self.assertNotIn("127.0.0.1:8080/readyz", dockerfile)

    def test_serve_cli_reports_invalid_cloud_configuration_without_traceback(self):
        from weex_mcp.__main__ import main

        stderr = io.StringIO()
        with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stderr(stderr):
            result = main(["serve"])

        self.assertEqual(result, 2)
        self.assertIn("WEEX_APPROVAL_TOKEN_SECRET", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
