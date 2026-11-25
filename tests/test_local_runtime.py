from __future__ import annotations

import importlib
import importlib.util
import json
import unittest
from unittest.mock import patch


class LocalRuntimeTests(unittest.TestCase):
    def _local_module(self):
        spec = importlib.util.find_spec("weex_mcp.local")
        self.assertIsNotNone(spec, "weex_mcp.local composition module is missing")
        return importlib.import_module("weex_mcp.local")

    def _build_service(self, *, trade_enabled: bool, account_adapter=None, partner_adapter=None):
        from weex_mcp.secret_provider import UnconfiguredSecretProvider
        from weex_mcp.settings import Settings

        local = self._local_module()
        with patch("weex_mcp.service.WeexBrokerOAuthAdapter") as broker_adapter:
            service = local.build_local_user_service(
                settings=Settings(trade_enabled=trade_enabled),
                secret_provider=UnconfiguredSecretProvider(),
                account_adapter=account_adapter,
                partner_adapter=partner_adapter,
            )
        broker_adapter.assert_not_called()
        return service

    def test_local_service_projects_catalog_credentials_and_discovery(self):
        service = self._build_service(trade_enabled=False)

        listed = service.handle_jsonrpc(
            {"jsonrpc": "2.0", "id": "local-list", "method": "tools/list", "params": {}}
        )
        tools = {tool["name"]: tool for tool in listed["result"]["tools"]}
        self.assertEqual(len(tools), 31)
        self.assertFalse(any(name.startswith("trade_") for name in tools))
        self.assertFalse(any(tool["_meta"]["com.weex/mcp"]["module"] == "broker" for tool in tools.values()))
        self.assertEqual(
            sum(tool["_meta"]["com.weex/mcp"]["module"] == "partner" for tool in tools.values()),
            7,
        )

        credential = tools["account_get_balance"]["inputSchema"]["properties"]["credential"]
        self.assertEqual([variant["properties"]["type"]["const"] for variant in credential["oneOf"]], ["credential_ref"])

        discovered = service.handle_jsonrpc(
            {"jsonrpc": "2.0", "id": "local-discover", "method": "server/discover", "params": {}}
        )
        self.assertNotIn("Broker OAuth", discovered["result"]["instructions"])
        self.assertNotIn("preview/confirm trading", discovered["result"]["instructions"])
        self.assertIn("read-only Partner queries", discovered["result"]["instructions"])

        disabled_trade = service.handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": "local-disabled-trade",
                "method": "tools/call",
                "params": {"name": "trade_preview_order", "arguments": {}},
            }
        )
        self.assertEqual(disabled_trade["result"]["structuredContent"]["error"]["code"], "module_disabled")

        for name in (
            "broker_oauth_generate_authorization_url",
            "broker_oauth_exchange_code_for_api_key",
        ):
            with self.subTest(name=name):
                called = service.handle_jsonrpc(
                    {
                        "jsonrpc": "2.0",
                        "id": name,
                        "method": "tools/call",
                        "params": {"name": name, "arguments": {}},
                    }
                )
                self.assertNotIn("result", called)
                self.assertEqual(called["error"]["code"], -32602)

    def test_local_plaintext_credential_is_rejected_before_account_adapter(self):
        class AccountAdapter:
            calls = 0

            def get_balance(self, **_kwargs):
                self.calls += 1
                return {"balances": []}

        adapter = AccountAdapter()
        service = self._build_service(trade_enabled=False, account_adapter=adapter)
        response = service.handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": "local-plaintext",
                "method": "tools/call",
                "params": {
                    "name": "account_get_balance",
                    "arguments": {
                        "market": "spot",
                        "trading_mode": "live",
                        "credential": {
                            "type": "plaintext",
                            "api_key": "AK",
                            "api_secret": "SK",
                            "api_passphrase": "PP",
                        },
                    },
                },
            }
        )

        self.assertEqual(response["result"]["structuredContent"]["error"]["code"], "invalid_arguments")
        self.assertEqual(adapter.calls, 0)

    def test_local_trade_enabled_catalog_contains_all_non_broker_tools(self):
        service = self._build_service(trade_enabled=True)

        listed = service.handle_jsonrpc(
            {"jsonrpc": "2.0", "id": "local-trade-list", "method": "tools/list", "params": {}}
        )
        names = {tool["name"] for tool in listed["result"]["tools"]}
        self.assertEqual(len(names), 57)
        self.assertEqual(sum(name.startswith("trade_") for name in names), 26)
        self.assertFalse(any(name.startswith("broker_oauth_") for name in names))
        self.assertEqual(sum(name.startswith("partner_") for name in names), 7)

        discovered = service.handle_jsonrpc(
            {"jsonrpc": "2.0", "id": "local-trade-discover", "method": "server/discover", "params": {}}
        )
        self.assertIn("preview/confirm trading", discovered["result"]["instructions"])
        self.assertIn("read-only Partner queries", discovered["result"]["instructions"])
        self.assertNotIn("Broker OAuth", discovered["result"]["instructions"])

    def test_local_partner_schema_is_ref_only_and_dispatches_injected_adapter(self):
        class PartnerAdapter:
            calls = 0

            def verify_referrals(self, **kwargs):
                self.calls += 1
                self.credential = kwargs["credential_argument"]
                return {
                    "endpoint": "partner.verify-referrals",
                    "method": "GET",
                    "path": "/api/v3/agency/verifyReferrals",
                    "weight": 10,
                    "rate_limit": {"used": {}, "remaining": {}},
                    "retry": {"automatic": False},
                    "environment": "partner_production",
                    "capability_mode": "read_only_query",
                    "data": [],
                }

        adapter = PartnerAdapter()
        service = self._build_service(trade_enabled=False, partner_adapter=adapter)
        listed = service.handle_jsonrpc(
            {"jsonrpc": "2.0", "id": "partner-list", "method": "tools/list", "params": {}}
        )
        tools = {tool["name"]: tool for tool in listed["result"]["tools"]}
        credential = tools["partner_verify_referrals"]["inputSchema"]["properties"]["credential"]
        self.assertEqual(
            [variant["properties"]["type"]["const"] for variant in credential["oneOf"]],
            ["credential_ref"],
        )
        response = service.handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": "partner-call",
                "method": "tools/call",
                "params": {
                    "name": "partner_verify_referrals",
                    "arguments": {
                        "credential": {"type": "credential_ref", "key_id": "key", "ref": "ref"},
                        "user_ids": ["1"],
                    },
                },
            }
        )
        self.assertTrue(response["result"]["structuredContent"]["ok"])
        self.assertEqual(adapter.calls, 1)
        self.assertEqual(adapter.credential["ref"], "ref")

        plaintext = service.handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": "partner-plaintext",
                "method": "tools/call",
                "params": {
                    "name": "partner_verify_referrals",
                    "arguments": {
                        "credential": {
                            "type": "plaintext",
                            "api_key": "AK",
                            "api_secret": "SK",
                            "api_passphrase": "PP",
                        },
                        "user_ids": ["1"],
                    },
                },
            }
        )
        self.assertEqual(plaintext["result"]["structuredContent"]["error"]["code"], "invalid_arguments")
        self.assertEqual(adapter.calls, 1)

    def test_local_approval_binding_is_route_bound_and_cross_instance_stable(self):
        from weex_mcp.approval_token import ApprovalTokenError
        from weex_mcp.secret_provider import UnconfiguredSecretProvider
        from weex_mcp.settings import Settings

        local = self._local_module()
        settings = Settings(
            trade_enabled=True,
            approval_token_secret="s" * 32,
            spot_base_url="https://local-spot.weex.tech/",
            futures_base_url="https://local-futures.weex.tech/",
        )
        first = local.build_local_user_service(
            settings=settings,
            secret_provider=UnconfiguredSecretProvider(),
        )
        second = local.build_local_user_service(
            settings=settings,
            secret_provider=UnconfiguredSecretProvider(),
        )
        token = first.approval_codec.issue(
            action="order",
            credential_fingerprint="fp_credential",
            summary={"action": "order"},
            risk={"status": "review_required"},
            risk_signature="risk-1",
        )

        claims = second.approval_codec.verify(token)
        self.assertEqual(claims["binding"].get("host_kind"), "local_user")
        self.assertTrue(str(claims["binding"].get("route_fingerprint") or "").startswith("fp_"))
        serialized_binding = json.dumps(claims["binding"], sort_keys=True)
        self.assertNotIn(settings.spot_base_url, serialized_binding)
        self.assertNotIn(settings.futures_base_url, serialized_binding)

        changed_route = local.build_local_user_service(
            settings=Settings(
                trade_enabled=True,
                approval_token_secret="s" * 32,
                spot_base_url="https://other-spot.weex.tech",
                futures_base_url=settings.futures_base_url,
            ),
            secret_provider=UnconfiguredSecretProvider(),
        )
        with self.assertRaises(ApprovalTokenError):
            changed_route.approval_codec.verify(token)

    def test_same_secret_local_token_is_rejected_by_broker_composition(self):
        from weex_mcp.approval_token import ApprovalTokenError
        from weex_mcp.cloud import build_cloud_app
        from weex_mcp.secret_provider import UnconfiguredSecretProvider
        from weex_mcp.settings import Settings

        local = self._local_module()
        origin_settings = Settings(
            trade_enabled=True,
            approval_token_secret="s" * 32,
            spot_base_url="https://api-spot.weex.com",
            futures_base_url="https://api-contract.weex.com",
        )
        local_service = local.build_local_user_service(
            settings=origin_settings,
            secret_provider=UnconfiguredSecretProvider(),
        )
        token = local_service.approval_codec.issue(
            action="order",
            credential_fingerprint="fp_credential",
            summary={"action": "order"},
            risk={"status": "review_required"},
            risk_signature="risk-1",
        )
        environment = {
            "WEEX_APPROVAL_TOKEN_SECRET": "s" * 32,
            "WEEX_SERVICE_AUTH_TOKEN": "t" * 32,
            "WEEX_TRADE_ENABLED": "true",
            "WEEX_SPOT_BASE_URL": origin_settings.spot_base_url,
            "WEEX_FUTURES_BASE_URL": origin_settings.futures_base_url,
        }
        with patch("weex_mcp.cloud.build_asgi_app", side_effect=lambda *, service: service):
            broker_service = build_cloud_app(environment)

        with self.assertRaises(ApprovalTokenError):
            broker_service.approval_codec.verify(token)


if __name__ == "__main__":
    unittest.main()
