import json
import os
import pathlib
import subprocess
import sys
import tomllib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class PackageContractTests(unittest.TestCase):
    def test_project_metadata_is_ready_for_library_distribution(self):
        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())
        project = metadata["project"]
        self.assertIn("readme", project)
        self.assertIn("license", project)
        self.assertTrue(project.get("urls"))
        self.assertTrue(project.get("optional-dependencies", {}).get("dev"))
        self.assertIn("uvicorn", " ".join(project.get("optional-dependencies", {}).get("server", [])).lower())
        self.assertIn("dynamic", project)
        self.assertIn("version", project["dynamic"])
        self.assertNotIn("version", project)

    def test_library_delivery_files_and_embed_boundary_are_documented(self):
        self.assertTrue((ROOT / "README.md").is_file())
        self.assertTrue((ROOT / "LICENSE").is_file())
        self.assertTrue((ROOT / ".gitignore").is_file())
        readme = (ROOT / "README.md").read_text()
        self.assertIn("build_default_service", readme)
        self.assertIn("host", readme.lower())
        self.assertIn("not", readme.lower())

    def test_cloud_delivery_files_and_dual_mode_usage_are_documented(self):
        self.assertTrue((ROOT / "Dockerfile").is_file())
        self.assertTrue((ROOT / ".dockerignore").is_file())
        self.assertTrue((ROOT / "DEPLOYMENT.md").is_file())
        readme = (ROOT / "README.md").read_text()
        deployment = (ROOT / "DEPLOYMENT.md").read_text()
        self.assertIn("weex-mcp serve", readme)
        self.assertIn("build_asgi_app", readme)
        self.assertIn("WEEX_APPROVAL_TOKEN_SECRET", deployment)
        self.assertIn("WEEX_SERVICE_AUTH_TOKEN", deployment)

    def test_ci_installs_and_checks_the_cloud_server_extra(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
        self.assertIn(".[dev,server]", workflow)
        self.assertIn("weex-mcp serve --help", workflow)

    def test_runtime_and_package_versions_share_one_source(self):
        from weex_mcp import __version__
        from weex_mcp.settings import Settings

        self.assertEqual(Settings().server_version, __version__)

    def test_stdio_cli_negotiates_legacy_protocol_and_lists_tools(self):
        requests = "\n".join(
            json.dumps(request)
            for request in (
                {
                    "jsonrpc": "2.0",
                    "id": "stdio-init",
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "stdio-test", "version": "1"},
                    },
                },
                {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
                {"jsonrpc": "2.0", "id": "stdio-list", "method": "tools/list", "params": {}},
            )
        ) + "\n"
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(ROOT / "src")
        completed = subprocess.run(
            [sys.executable, "-m", "weex_mcp", "stdio"],
            input=requests,
            text=True,
            capture_output=True,
            timeout=5,
            cwd=ROOT,
            env=environment,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        responses = [json.loads(line) for line in completed.stdout.splitlines()]
        self.assertEqual(len(responses), 2)
        self.assertEqual(responses[0]["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(len(responses[1]["result"]["tools"]), 17)
        self.assertNotIn("resultType", responses[1]["result"])


if __name__ == "__main__":
    unittest.main()
