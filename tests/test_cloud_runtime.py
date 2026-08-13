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
            "WEEX_SERVICE_AUTH_TOKEN": "T" * 48,
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
        self.assertNotIn("T" * 48, repr(settings))

    def test_service_bearer_requires_at_least_32_ascii_characters(self):
        from weex_mcp.settings import Settings

        with self.assertRaisesRegex(ValueError, "at least 32"):
            Settings.from_environment(
                self._cloud_env() | {"WEEX_SERVICE_AUTH_TOKEN": "short-service-token"},
                require_cloud=True,
            )
        with self.assertRaisesRegex(ValueError, "at least 32"):
            Settings(service_auth_token="x" * 31)

        self.assertEqual(Settings(service_auth_token="x" * 32).service_auth_token, "x" * 32)

    def test_environment_parses_https_upstreams_and_market_retry_settings(self):
        from weex_mcp.settings import Settings

        settings = Settings.from_environment(
            self._cloud_env()
            | {
                "WEEX_SPOT_BASE_URL": "https://stg-spotpro-openapi.weex.tech/",
                "WEEX_FUTURES_BASE_URL": "https://stg-api-contract.weex.tech",
                "WEEX_MARKET_MAX_ATTEMPTS": "4",
                "WEEX_MARKET_RETRY_BASE_DELAY_SECONDS": "0.25",
            },
            require_cloud=True,
        )

        self.assertEqual(getattr(settings, "spot_base_url", None), "https://stg-spotpro-openapi.weex.tech")
        self.assertEqual(getattr(settings, "futures_base_url", None), "https://stg-api-contract.weex.tech")
        self.assertEqual(getattr(settings, "market_max_attempts", None), 4)
        self.assertEqual(getattr(settings, "market_retry_base_delay_seconds", None), 0.25)

    def test_environment_rejects_unsafe_weex_upstream_urls(self):
        from weex_mcp.settings import Settings

        invalid_urls = (
            "http://stg-api-contract.weex.tech",
            "https://user:pass@stg-api-contract.weex.tech",
            "https://stg-api-contract.weex.tech/private/path",
            "https://stg-api-contract.weex.tech?target=other",
            "https://stg-api-contract.weex.tech#fragment",
        )
        for value in invalid_urls:
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "WEEX_FUTURES_BASE_URL"):
                    Settings.from_environment(
                        self._cloud_env() | {"WEEX_FUTURES_BASE_URL": value},
                        require_cloud=True,
                    )

    def test_default_service_wires_deployment_upstreams_to_all_rest_adapters(self):
        from weex_mcp.service import build_default_service
        from weex_mcp.settings import Settings

        try:
            settings = Settings(
                trade_enabled=True,
                spot_base_url="https://stg-spotpro-openapi.weex.tech",
                futures_base_url="https://stg-api-contract.weex.tech",
            )
        except TypeError as exc:
            self.fail(str(exc))
        service = build_default_service(settings=settings)

        for adapter in (service.market_adapter, service.account_adapter, service.trade_adapter):
            with self.subTest(adapter=adapter.__class__.__name__):
                self.assertEqual(adapter.spot_base_url, settings.spot_base_url)
                self.assertEqual(adapter.futures_base_url, settings.futures_base_url)
        self.assertEqual(service.market_adapter.max_attempts, settings.market_max_attempts)

    def test_environment_rejects_invalid_values_before_server_start(self):
        from weex_mcp.settings import Settings

        invalid_values = {
            "WEEX_TRADE_ENABLED": "sometimes",
            "WEEX_APPROVAL_TOKEN_TTL_SECONDS": "0",
            "WEEX_MAX_REQUEST_BYTES": "-1",
            "WEEX_MARKET_TIMEOUT_SECONDS": "nan",
            "WEEX_ALLOWED_ORIGINS": "*",
            "WEEX_MARKET_MAX_ATTEMPTS": "0",
            "WEEX_MARKET_RETRY_BASE_DELAY_SECONDS": "-1",
        }
        for name, value in invalid_values.items():
            with self.subTest(name=name):
                environment = self._cloud_env() | {name: value}
                with self.assertRaisesRegex(ValueError, name):
                    Settings.from_environment(environment, require_cloud=True)

    def test_direct_settings_reject_non_integer_market_attempt_counts(self):
        from weex_mcp.settings import Settings

        for value in (True, 1.5, "3"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "market_max_attempts"):
                    Settings(market_max_attempts=value)

    def test_direct_settings_reject_invalid_public_configuration(self):
        from weex_mcp.settings import Settings, SettingsError

        invalid_values = (
            ("trade_enabled", "false"),
            ("approval_token_secret", "too-short"),
            ("approval_token_secret", 123),
            ("approval_token_ttl_seconds", True),
            ("approval_token_ttl_seconds", 0),
            ("approval_token_ttl_seconds", "120"),
            ("server_name", "   "),
            ("server_version", ""),
            ("protocol_version", ""),
            ("max_request_bytes", True),
            ("max_request_bytes", 0),
            ("max_request_bytes", "1024"),
            ("market_timeout_seconds", True),
            ("market_timeout_seconds", 0),
            ("market_timeout_seconds", "5"),
            ("market_retry_base_delay_seconds", True),
            ("market_retry_base_delay_seconds", -1),
            ("market_retry_base_delay_seconds", "0.1"),
            ("account_timeout_seconds", True),
            ("account_timeout_seconds", 0),
            ("account_timeout_seconds", "5"),
            ("service_auth_token", 123),
            ("allowed_origins", ["https://mcp.example.com"]),
            ("allowed_origins", ("*",)),
        )
        for name, value in invalid_values:
            with self.subTest(name=name, value=value):
                with self.assertRaisesRegex(SettingsError, name):
                    Settings(**{name: value})

    def test_direct_settings_normalizes_public_configuration(self):
        from weex_mcp.settings import Settings

        settings = Settings(
            server_name="  embedded-weex-mcp  ",
            server_version="  1.2.3  ",
            protocol_version="  2099-01-01  ",
            allowed_origins=("https://mcp.example.com/", "https://mcp.example.com"),
        )

        self.assertEqual(settings.server_name, "embedded-weex-mcp")
        self.assertEqual(settings.server_version, "1.2.3")
        self.assertEqual(settings.protocol_version, "2099-01-01")
        self.assertEqual(settings.allowed_origins, ("https://mcp.example.com",))

    def test_default_service_rejects_non_boolean_trade_override(self):
        from weex_mcp.service import build_default_service

        with self.assertRaisesRegex(ValueError, "trade_enabled"):
            build_default_service(trade_enabled="false")

    def test_service_bearer_rejects_non_ascii_values_for_reliable_http_comparison(self):
        from weex_mcp.settings import Settings

        with self.assertRaisesRegex(ValueError, "ASCII"):
            Settings.from_environment(
                self._cloud_env() | {"WEEX_SERVICE_AUTH_TOKEN": ("x" * 31) + "é"},
                require_cloud=True,
            )
        with self.assertRaisesRegex(ValueError, "ASCII"):
            Settings(service_auth_token=("x" * 31) + "é")

    def test_local_environment_still_allows_embedded_defaults(self):
        from weex_mcp.service import build_default_service
        from weex_mcp.settings import Settings

        settings = Settings.from_environment({}, require_cloud=False)
        service = build_default_service(settings=settings)

        self.assertEqual(settings.service_auth_token, "")
        self.assertTrue(service.settings.trade_enabled)

    def test_cloud_app_exposes_readyz_without_exposing_secrets(self):
        from weex_mcp.cloud import build_cloud_app

        app = build_cloud_app(self._cloud_env())
        status, body = asyncio.run(_call_asgi(app, method="GET", path="/readyz"))
        payload = json.loads(body)

        self.assertEqual(status, 200)
        self.assertTrue(payload["ready"])
        self.assertNotIn("S" * 48, body.decode())
        self.assertNotIn("T" * 48, body.decode())

    def test_serve_cli_keeps_local_flags_and_dispatches_cloud_server(self):
        from weex_mcp.__main__ import main

        with patch.dict(os.environ, self._cloud_env(), clear=True), patch("weex_mcp.cloud.run_cloud_server") as run_server:
            result = main(["serve", "--host", "0.0.0.0", "--port", "9090", "--workers", "2"])

        self.assertEqual(result, 0)
        run_server.assert_called_once_with(host="0.0.0.0", port=9090, workers=2)

    def test_serve_cli_and_docker_healthcheck_share_the_environment_port(self):
        from pathlib import Path

        from weex_mcp.__main__ import main

        environment = self._cloud_env() | {"WEEX_MCP_PORT": "9191"}
        with patch.dict(os.environ, environment, clear=True), patch("weex_mcp.cloud.run_cloud_server") as run_server:
            result = main(["serve"])

        self.assertEqual(result, 0)
        run_server.assert_called_once_with(host="127.0.0.1", port=9191, workers=1)
        dockerfile = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text()
        self.assertIn("WEEX_MCP_PORT=8080", dockerfile)
        self.assertIn("os.environ", dockerfile)
        self.assertNotIn("127.0.0.1:8080/readyz", dockerfile)
        self.assertIn('["weex-mcp", "serve", "--host", "0.0.0.0"', dockerfile)

    def test_serve_cli_allows_an_explicit_environment_bind_host(self):
        from weex_mcp.__main__ import main

        environment = self._cloud_env() | {"WEEX_MCP_BIND_HOST": "192.168.1.20"}
        with patch.dict(os.environ, environment, clear=True), patch("weex_mcp.cloud.run_cloud_server") as run_server:
            result = main(["serve"])

        self.assertEqual(result, 0)
        run_server.assert_called_once_with(host="192.168.1.20", port=8080, workers=1)

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
