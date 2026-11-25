from __future__ import annotations

import copy
import hashlib
import inspect
import json
import pathlib
import re
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


def _canonical_digest(value):
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _discover(service, *, protocol_version=None):
    response = service.handle_jsonrpc(
        {"jsonrpc": "2.0", "id": "discover", "method": "server/discover", "params": {}},
        protocol_version=protocol_version,
    )
    return response, response["result"]["_meta"]["com.weex/mcp"]["compatibility"]


class CompatibilityManifestTests(unittest.TestCase):
    def test_manifest_is_deterministic_and_only_schema_changes_schema_digest(self):
        from weex_mcp.compatibility import build_compatibility_manifest
        from weex_mcp.tool_registry import build_default_registry

        self.assertNotIn("trade_enabled", inspect.signature(build_compatibility_manifest).parameters)
        tools = build_default_registry(
            enabled_modules={"market", "account"},
            credential_modes={"credential_ref"},
        ).list_tools()
        first = build_compatibility_manifest(
            package_version="0.2.0",
            runtime_profile="default",
            tools=list(reversed(tools)),
        )
        second = build_compatibility_manifest(
            package_version="0.2.0",
            runtime_profile="default",
            tools=copy.deepcopy(tools),
        )

        self.assertEqual(first, second)
        self.assertEqual(first["manifest_version"], 1)
        self.assertEqual(first["tool_contract_version"], "1")
        self.assertEqual(first["runtime_profile"], "default")
        self.assertEqual(first["credential_modes"], ["credential_ref"])
        self.assertEqual(first["modules"], ["account", "market"])
        self.assertFalse(first["delegated_authorization"])
        self.assertEqual(first["tools"], sorted(first["tools"]))
        for field in ("tool_set_digest", "schema_digest", "manifest_digest"):
            self.assertRegex(first[field], DIGEST_PATTERN)

        prose_only = copy.deepcopy(tools)
        prose_only[0]["title"] = "Changed title only"
        prose_only[0]["description"] = "Changed description only"
        prose_manifest = build_compatibility_manifest(
            package_version="0.2.0",
            runtime_profile="default",
            tools=prose_only,
        )
        self.assertEqual(prose_manifest, first)

        schema_changed = copy.deepcopy(tools)
        schema_changed[0]["inputSchema"]["properties"]["compatibility_probe"] = {
            "type": "string"
        }
        changed_manifest = build_compatibility_manifest(
            package_version="0.2.0",
            runtime_profile="default",
            tools=schema_changed,
        )
        self.assertEqual(changed_manifest["tool_set_digest"], first["tool_set_digest"])
        self.assertNotEqual(changed_manifest["schema_digest"], first["schema_digest"])
        self.assertNotEqual(changed_manifest["manifest_digest"], first["manifest_digest"])

        market_only = build_compatibility_manifest(
            package_version="0.2.0",
            runtime_profile="default",
            tools=build_default_registry(enabled_modules={"market"}).list_tools(),
        )
        self.assertFalse(market_only["trade_enabled"])

    def test_duplicate_tools_and_manifest_tampering_fail_digest_contract(self):
        from weex_mcp.compatibility import build_compatibility_manifest
        from weex_mcp.tool_registry import build_default_registry

        tools = build_default_registry(enabled_modules={"market"}).list_tools()
        with self.assertRaisesRegex(ValueError, "duplicate"):
            build_compatibility_manifest(
                package_version="0.2.0",
                runtime_profile="default",
                tools=[tools[0], copy.deepcopy(tools[0])],
            )

        non_finite = copy.deepcopy(tools[:1])
        non_finite[0]["inputSchema"]["properties"]["bad_default"] = {
            "type": "number",
            "default": float("nan"),
        }
        with self.assertRaises(ValueError):
            build_compatibility_manifest(
                package_version="0.2.0",
                runtime_profile="default",
                tools=non_finite,
            )

        manifest = build_compatibility_manifest(
            package_version="0.2.0",
            runtime_profile="default",
            tools=tools,
        )
        payload = dict(manifest)
        stored_digest = payload.pop("manifest_digest")
        self.assertEqual(stored_digest, _canonical_digest(payload))

        tampered = copy.deepcopy(manifest)
        tampered["tools"].append("market_tampered")
        tampered_payload = dict(tampered)
        tampered_stored = tampered_payload.pop("manifest_digest")
        self.assertNotEqual(tampered_stored, _canonical_digest(tampered_payload))

    def test_local_and_broker_trade_profiles_publish_actual_effective_contracts(self):
        from weex_mcp.cloud import build_cloud_app
        from weex_mcp.local import build_local_user_service
        from weex_mcp.secret_provider import UnconfiguredSecretProvider
        from weex_mcp.settings import Settings

        local_services = {
            False: build_local_user_service(
                settings=Settings(trade_enabled=False),
                secret_provider=UnconfiguredSecretProvider(),
            ),
            True: build_local_user_service(
                settings=Settings(trade_enabled=True, approval_token_secret="l" * 32),
                secret_provider=UnconfiguredSecretProvider(),
            ),
        }
        cloud_base = {
            "WEEX_APPROVAL_TOKEN_SECRET": "S" * 48,
            "WEEX_SERVICE_AUTH_TOKEN": "T" * 48,
        }
        with patch("weex_mcp.cloud.build_asgi_app", side_effect=lambda *, service: service):
            broker_services = {
                False: build_cloud_app(cloud_base),
                True: build_cloud_app(cloud_base | {"WEEX_TRADE_ENABLED": "true"}),
            }

        manifests = {}
        for trade_enabled, service in local_services.items():
            manifest = _discover(service)[1]
            manifests[("local_user", trade_enabled)] = manifest
            self.assertEqual(manifest["runtime_profile"], "local_user")
            self.assertEqual(manifest["trade_enabled"], trade_enabled)
            self.assertEqual(len(manifest["tools"]), 57 if trade_enabled else 31)
            self.assertEqual(
                manifest["modules"],
                ["account", "market", "partner", "trade"]
                if trade_enabled
                else ["account", "market", "partner"],
            )
            self.assertEqual(manifest["credential_modes"], ["credential_ref"])
            self.assertEqual(manifest["delegated_authorization"], trade_enabled)

        for trade_enabled, service in broker_services.items():
            manifest = _discover(service)[1]
            manifests[("broker_server", trade_enabled)] = manifest
            self.assertEqual(manifest["runtime_profile"], "broker_server")
            self.assertEqual(manifest["trade_enabled"], trade_enabled)
            self.assertEqual(len(manifest["tools"]), 52 if trade_enabled else 26)
            self.assertEqual(
                manifest["modules"],
                ["account", "broker", "market", "trade"]
                if trade_enabled
                else ["account", "broker", "market"],
            )
            self.assertEqual(manifest["credential_modes"], ["plaintext"])
            self.assertFalse(manifest["delegated_authorization"])

        self.assertEqual(len({item["manifest_digest"] for item in manifests.values()}), 4)
        serialized = json.dumps(list(manifests.values()), sort_keys=True)
        for secret in ("S" * 48, "T" * 48, "api-spot.weex.com", "credential-ref-value"):
            self.assertNotIn(secret, serialized)

    def test_default_profile_and_legacy_current_discovery_share_compatibility_metadata(self):
        from weex_mcp.service import build_default_service

        service = build_default_service()
        modern, modern_manifest = _discover(service)
        self.assertEqual(modern_manifest["runtime_profile"], "default")
        self.assertFalse(modern_manifest["trade_enabled"])
        self.assertEqual(len(modern_manifest["tools"]), 26)
        self.assertEqual(
            modern_manifest["credential_modes"],
            ["credential_ref", "encrypted_envelope", "plaintext"],
        )
        self.assertEqual(modern_manifest["modules"], ["account", "broker", "market"])
        self.assertFalse(modern_manifest["delegated_authorization"])
        self.assertIn("resultType", modern["result"])

        for version in ("2025-06-18", "2025-11-25"):
            with self.subTest(version=version):
                legacy, legacy_manifest = _discover(service, protocol_version=version)
                self.assertEqual(legacy_manifest, modern_manifest)
                self.assertNotIn("resultType", legacy["result"])

    def test_runtime_profile_invariants_reject_mislabelled_effective_contracts(self):
        from weex_mcp.service import build_default_service
        from weex_mcp.settings import Settings
        from weex_mcp.tool_registry import build_default_registry

        invalid = (
            {
                "runtime_profile": "local_user",
                "settings": Settings(trade_enabled=False),
                "registry": build_default_registry(),
            },
            {
                "runtime_profile": "local_user",
                "settings": Settings(trade_enabled=False),
                "registry": build_default_registry(
                    enabled_modules={"account", "market", "partner", "trade"},
                    credential_modes={"plaintext"},
                    allow_delegated_authorization=True,
                ),
            },
            {
                "runtime_profile": "local_user",
                "settings": Settings(trade_enabled=True),
                "registry": build_default_registry(
                    enabled_modules={"account", "market", "partner", "trade"},
                    credential_modes={"credential_ref"},
                    allow_delegated_authorization=False,
                ),
            },
            {
                "runtime_profile": "broker_server",
                "settings": Settings(trade_enabled=False),
                "registry": build_default_registry(
                    enabled_modules={"account", "broker", "market", "trade"},
                    credential_modes={"credential_ref"},
                ),
            },
            {
                "runtime_profile": "broker_server",
                "settings": Settings(trade_enabled=False),
                "registry": build_default_registry(
                    enabled_modules={"account", "broker", "market", "partner", "trade"},
                    credential_modes={"plaintext"},
                ),
            },
            {
                "runtime_profile": "broker_server",
                "settings": Settings(trade_enabled=True),
                "registry": build_default_registry(
                    enabled_modules={"account", "broker", "market", "trade"},
                    credential_modes={"plaintext"},
                    allow_delegated_authorization=True,
                ),
            },
        )
        for kwargs in invalid:
            with self.subTest(runtime_profile=kwargs["runtime_profile"]):
                with self.assertRaisesRegex(ValueError, "runtime profile"):
                    build_default_service(**kwargs)

    def test_unpublished_version_source_package_and_docs_contract(self):
        from weex_mcp import __version__

        server = json.loads((ROOT / "server.json").read_text())
        self.assertEqual(__version__, "0.2.0")
        self.assertEqual(server["version"], __version__)
        self.assertEqual(server["packages"][0]["version"], __version__)
        self.assertTrue((ROOT / "src" / "weex_mcp" / "compatibility.py").is_file())
        self.assertFalse((ROOT / "src" / "weex_mcp" / "compatibility.json").exists())
        package_init = (ROOT / "src" / "weex_mcp" / "__init__.py").read_text()
        self.assertNotIn("compatibility", package_init)

        readme = (ROOT / "README.md").read_text()
        deployment = (ROOT / "DEPLOYMENT.md").read_text()
        for required in (
            "26",
            "52",
            "31",
            "57",
            "Local User",
            "Broker Server",
            "Partner",
            "compatibility",
            "schema_digest",
        ):
            with self.subTest(document="README", required=required):
                self.assertIn(required, readme)
        for required in (
            "0.2.0",
            "unpublished",
            "manifest_digest",
            "tool_set_digest",
            "schema_digest",
            "candidate artifact",
            "exact artifact",
            "rollback",
        ):
            with self.subTest(document="DEPLOYMENT", required=required):
                self.assertIn(required, deployment)


if __name__ == "__main__":
    unittest.main()
