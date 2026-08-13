from __future__ import annotations

import io
import json
import unittest


class StdioServerTests(unittest.TestCase):
    def _run(self, *requests: dict[str, object]) -> list[dict[str, object]]:
        from weex_mcp.stdio_server import run_stdio_server

        stdin = io.StringIO("".join(f"{json.dumps(request)}\n" for request in requests))
        stdout = io.StringIO()
        self.assertEqual(run_stdio_server(stdin=stdin, stdout=stdout), 0)
        return [json.loads(line) for line in stdout.getvalue().splitlines()]

    def test_modern_request_requires_protocol_metadata(self):
        responses = self._run(
            {"jsonrpc": "2.0", "id": "missing-meta", "method": "tools/list", "params": {}}
        )

        self.assertEqual(len(responses), 1)
        self.assertEqual(responses[0]["error"]["code"], -32602)
        self.assertIn("protocol_version_missing", responses[0]["error"]["message"])

    def test_modern_request_requires_protocol_version_inside_metadata(self):
        responses = self._run(
            {
                "jsonrpc": "2.0",
                "id": "missing-version",
                "method": "tools/list",
                "params": {
                    "_meta": {"io.modelcontextprotocol/clientCapabilities": {}}
                },
            }
        )

        self.assertEqual(responses[0]["error"]["code"], -32602)
        self.assertIn("protocol_version_missing", responses[0]["error"]["message"])

    def test_modern_request_rejects_unsupported_protocol_version(self):
        responses = self._run(
            {
                "jsonrpc": "2.0",
                "id": "unsupported-version",
                "method": "tools/list",
                "params": {
                    "_meta": {
                        "io.modelcontextprotocol/protocolVersion": "1900-01-01",
                        "io.modelcontextprotocol/clientCapabilities": {},
                    }
                },
            }
        )

        self.assertEqual(len(responses), 1)
        self.assertEqual(responses[0]["error"]["code"], -32022)
        self.assertEqual(responses[0]["error"]["data"]["supported"], ["2026-07-28"])
        self.assertEqual(responses[0]["error"]["data"]["requested"], "1900-01-01")

    def test_modern_request_with_required_metadata_succeeds(self):
        responses = self._run(
            {
                "jsonrpc": "2.0",
                "id": "modern-list",
                "method": "tools/list",
                "params": {
                    "_meta": {
                        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                        "io.modelcontextprotocol/clientCapabilities": {},
                    }
                },
            }
        )

        self.assertEqual(len(responses), 1)
        self.assertEqual(len(responses[0]["result"]["tools"]), 47)
        self.assertEqual(sum(tool["name"].startswith("trade_") for tool in responses[0]["result"]["tools"]), 26)

    def test_legacy_initialize_still_negotiates_the_stdio_session(self):
        for version in ("2025-06-18", "2025-11-25"):
            with self.subTest(version=version):
                responses = self._run(
                    {
                        "jsonrpc": "2.0",
                        "id": "legacy-init",
                        "method": "initialize",
                        "params": {
                            "protocolVersion": version,
                            "capabilities": {},
                            "clientInfo": {"name": "test", "version": "1"},
                        },
                    },
                    {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
                    {"jsonrpc": "2.0", "id": "legacy-list", "method": "tools/list", "params": {}},
                )

                self.assertEqual(responses[0]["result"]["protocolVersion"], version)
                self.assertEqual(len(responses[1]["result"]["tools"]), 47)
                self.assertEqual(sum(tool["name"].startswith("trade_") for tool in responses[1]["result"]["tools"]), 26)
                self.assertNotIn("resultType", responses[1]["result"])


if __name__ == "__main__":
    unittest.main()
