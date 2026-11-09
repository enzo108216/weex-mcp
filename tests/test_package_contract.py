import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile
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
        self.assertIn("Development Status :: 4 - Beta", project.get("classifiers", []))
        self.assertIn("mcp", project.get("keywords", []))
        for url_name in ("Homepage", "Repository", "Issues", "Documentation"):
            self.assertIn(url_name, project["urls"])
            self.assertIn("github.com/weex-labs/weex-mcp", project["urls"][url_name])

    def test_mcp_registry_manifest_matches_package_and_readme_marker(self):
        from weex_mcp import __version__

        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
        registry = json.loads((ROOT / "server.json").read_text())
        readme = (ROOT / "README.md").read_text()

        self.assertEqual(registry["name"], "io.github.weex-labs/weex-mcp")
        self.assertIn(f'<!-- mcp-name: {registry["name"]} -->', readme)
        self.assertEqual(registry["version"], __version__)
        self.assertEqual(registry["packages"][0]["registryType"], "pypi")
        self.assertEqual(registry["packages"][0]["identifier"], metadata["name"])
        self.assertEqual(registry["packages"][0]["version"], registry["version"])
        self.assertEqual(registry["packages"][0]["runtimeHint"], "uvx")
        self.assertEqual(
            registry["packages"][0]["packageArguments"],
            [{"type": "positional", "value": "stdio"}],
        )
        self.assertEqual(registry["packages"][0]["transport"]["type"], "stdio")
        self.assertEqual(registry["repository"]["url"], metadata["urls"]["Repository"])

    def test_library_delivery_files_and_embed_boundary_are_documented(self):
        self.assertTrue((ROOT / "README.md").is_file())
        self.assertTrue((ROOT / "LICENSE").is_file())
        self.assertTrue((ROOT / ".gitignore").is_file())
        readme = (ROOT / "README.md").read_text()
        self.assertIn("build_default_service", readme)
        self.assertIn("host", readme.lower())
        self.assertIn("not", readme.lower())

    def test_source_tree_excludes_uncontracted_rate_limiting(self):
        self.assertFalse((ROOT / "src" / "weex_mcp" / "rate_limit.py").exists())

    def test_readme_documents_clients_layout_development_and_troubleshooting(self):
        readme = (ROOT / "README.md").read_text()

        for required_text in (
            "## Contents",
            "### Codex",
            "### Claude Code",
            "## Project layout",
            "## Development and verification",
            "## Troubleshooting",
        ):
            with self.subTest(required_text=required_text):
                self.assertIn(required_text, readme)

        self.assertIn("python -m pip install weex-mcp", readme)
        self.assertIn("python -m pip install 'weex-mcp[server]'", readme)
        self.assertIn("python -m pip install -e '.[dev,server]'", readme)
        for repository_file in ("DEPLOYMENT.md", "KNOWN_ISSUES.md", "LICENSE"):
            with self.subTest(repository_file=repository_file):
                self.assertIn(
                    f"https://github.com/weex-labs/weex-mcp/blob/main/{repository_file}",
                    readme,
                )

    def test_external_staging_defect_has_a_complete_handoff_record(self):
        defect_record = ROOT / "KNOWN_ISSUES.md"
        self.assertTrue(defect_record.is_file())
        contents = defect_record.read_text()
        for required_text in (
            "Reproduction conditions",
            "Evidence and impact",
            "Owner",
            "Temporary handling",
            "Retest conditions",
            "Do not submit another write request",
        ):
            with self.subTest(required_text=required_text):
                self.assertIn(required_text, contents)

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
        self.assertIn("WEEX_SPOT_BASE_URL", deployment)
        self.assertIn("WEEX_FUTURES_BASE_URL", deployment)

    def test_ci_installs_and_checks_the_cloud_server_extra(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
        self.assertIn(".[dev,server]", workflow)
        self.assertIn("weex-mcp serve --help", workflow)
        self.assertIn('python-version: ["3.11", "3.12", "3.13"]', workflow)
        self.assertIn("python -m build --sdist --wheel", workflow)
        self.assertIn("twine check dist/*", workflow)
        self.assertIn("docker build", workflow)

    def test_ci_lints_the_demo_smoke_script(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
        self.assertRegex(workflow, r"ruff check[^\n]*src[^\n]*tests[^\n]*scripts")

    def test_ci_uses_tracked_env_template_for_compose_validation(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
        self.assertIn("WEEX_MCP_ENV_FILE=.env.example", workflow)

    def test_docker_compose_contract_has_cloud_secrets_port_and_readiness(self):
        compose = (ROOT / "compose.yaml").read_text()
        self.assertIn("env_file:", compose)
        self.assertIn("WEEX_APPROVAL_TOKEN_SECRET", compose)
        self.assertIn("WEEX_SERVICE_AUTH_TOKEN", compose)
        self.assertIn("WEEX_MCP_PORT", compose)
        self.assertIn("/readyz", compose)
        self.assertIn("unless-stopped", compose)
        self.assertIn("${WEEX_MCP_BIND_HOST:-127.0.0.1}", compose)

        example_values = dict(
            line.split("=", 1)
            for line in (ROOT / ".env.example").read_text().splitlines()
            if line and not line.startswith("#") and "=" in line
        )
        self.assertEqual(example_values["WEEX_APPROVAL_TOKEN_SECRET"], "")
        self.assertEqual(example_values["WEEX_SERVICE_AUTH_TOKEN"], "")
        self.assertEqual(example_values.get("WEEX_MCP_BIND_HOST"), "127.0.0.1")
        self.assertEqual(example_values.get("WEEX_SPOT_BASE_URL"), "https://api-spot.weex.com")
        self.assertEqual(example_values.get("WEEX_FUTURES_BASE_URL"), "https://api-contract.weex.com")

        readme = (ROOT / "README.md").read_text()
        self.assertIn("WEEX_MCP_BIND_HOST=0.0.0.0 ./scripts/docker-start.sh", readme)
        self.assertIn("WEEX_MCP_BIND_HOST=192.168.1.20 ./scripts/docker-start.sh", readme)

    def test_compose_forwards_shell_runtime_settings_over_env_file(self):
        if shutil.which("docker") is None:
            self.skipTest("Docker Compose is not installed")

        shell_settings = {
            "WEEX_APPROVAL_TOKEN_TTL_SECONDS": "321",
            "WEEX_ALLOWED_ORIGINS": "https://client.example.com",
            "WEEX_MAX_REQUEST_BYTES": "2097152",
            "WEEX_MARKET_TIMEOUT_SECONDS": "7.5",
            "WEEX_MARKET_MAX_ATTEMPTS": "4",
            "WEEX_MARKET_RETRY_BASE_DELAY_SECONDS": "0.25",
            "WEEX_ACCOUNT_TIMEOUT_SECONDS": "9.5",
            "WEEX_SPOT_BASE_URL": "https://stg-spot.example.com",
            "WEEX_FUTURES_BASE_URL": "https://stg-futures.example.com",
            "WEEX_SERVER_NAME": "weex-mcp-staging",
        }
        with tempfile.TemporaryDirectory(prefix="weex-mcp-compose-test-") as temp_dir:
            env_file = pathlib.Path(temp_dir) / "compose.env"
            env_file.write_text(
                "WEEX_APPROVAL_TOKEN_SECRET=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\n"
                "WEEX_SERVICE_AUTH_TOKEN=BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB\n"
                "WEEX_SPOT_BASE_URL=https://api-spot.weex.com\n"
                "WEEX_FUTURES_BASE_URL=https://api-contract.weex.com\n"
            )
            environment = os.environ.copy()
            environment.update(shell_settings | {"WEEX_MCP_ENV_FILE": str(env_file)})
            completed = subprocess.run(
                [
                    "docker",
                    "compose",
                    "--project-directory",
                    str(ROOT),
                    "--env-file",
                    str(env_file),
                    "config",
                    "--format",
                    "json",
                ],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        container_environment = json.loads(completed.stdout)["services"]["mcp"]["environment"]
        for name, value in shell_settings.items():
            with self.subTest(name=name):
                self.assertEqual(container_environment.get(name), value)

    def test_docker_start_generates_local_env_once_and_waits_for_readiness(self):
        script = ROOT / "scripts" / "docker-start.sh"
        self.assertTrue(script.is_file())
        self.assertTrue(script.stat().st_mode & stat.S_IXUSR)
        with tempfile.TemporaryDirectory(prefix="weex-mcp-docker-test-") as temp_dir:
            temp = pathlib.Path(temp_dir)
            bin_dir = temp / "bin"
            bin_dir.mkdir()
            log_path = temp / "docker.log"
            fake_docker = bin_dir / "docker"
            fake_docker.write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' \"$*\" >> \"$DOCKER_TEST_LOG\"\n"
                "exit 0\n"
            )
            fake_docker.chmod(0o755)
            env_file = temp / "generated.env"
            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{bin_dir}:{environment['PATH']}",
                    "DOCKER_TEST_LOG": str(log_path),
                    "WEEX_MCP_ENV_FILE": str(env_file),
                    "WEEX_MCP_PORT": "19080",
                    "WEEX_MCP_HOST_PORT": "19081",
                    "WEEX_MCP_BIND_HOST": "0.0.0.0",
                }
            )
            first = subprocess.run(
                [str(script)], cwd=ROOT, env=environment, text=True, capture_output=True, check=False
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            generated = env_file.read_text()
            values = dict(
                line.split("=", 1)
                for line in generated.splitlines()
                if line and not line.startswith("#") and "=" in line
            )
            self.assertGreaterEqual(len(values["WEEX_APPROVAL_TOKEN_SECRET"]), 32)
            self.assertGreaterEqual(len(values["WEEX_SERVICE_AUTH_TOKEN"]), 32)
            self.assertNotEqual(values["WEEX_APPROVAL_TOKEN_SECRET"], values["WEEX_SERVICE_AUTH_TOKEN"])
            self.assertEqual(values["WEEX_TRADE_ENABLED"], "false")
            self.assertEqual(values["WEEX_MCP_PORT"], "19080")
            self.assertEqual(values["WEEX_MCP_HOST_PORT"], "19081")
            self.assertEqual(values["WEEX_MCP_BIND_HOST"], "0.0.0.0")
            self.assertEqual(stat.S_IMODE(env_file.stat().st_mode), 0o600)
            self.assertIn("compose", log_path.read_text())
            self.assertIn("--wait", log_path.read_text())

            env_file.write_text(
                "WEEX_APPROVAL_TOKEN_SECRET=keep-me\n"
                "WEEX_SERVICE_AUTH_TOKEN=keep-me-too\n"
                "WEEX_MCP_HOST_PORT=18081\n"
            )
            env_file.chmod(0o600)
            second = subprocess.run(
                [str(script)], cwd=ROOT, env=environment, text=True, capture_output=True, check=False
            )
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(
                env_file.read_text(),
                "WEEX_APPROVAL_TOKEN_SECRET=keep-me\n"
                "WEEX_SERVICE_AUTH_TOKEN=keep-me-too\n"
                "WEEX_MCP_HOST_PORT=18081\n",
            )
            self.assertIn("WEEX MCP is published on 0.0.0.0:19081.", second.stdout)

            default_env_file = temp / "default.env"
            default_environment = environment | {"WEEX_MCP_ENV_FILE": str(default_env_file)}
            default_environment.pop("WEEX_MCP_BIND_HOST")
            default_run = subprocess.run(
                [str(script)], cwd=ROOT, env=default_environment, text=True, capture_output=True, check=False
            )
            self.assertEqual(default_run.returncode, 0, default_run.stderr)
            self.assertIn("WEEX_MCP_BIND_HOST=127.0.0.1", default_env_file.read_text())

    def test_runtime_and_package_versions_share_one_source(self):
        from weex_mcp import __version__
        from weex_mcp.settings import Settings

        self.assertEqual(Settings().server_version, __version__)

    def test_deployment_rollback_uses_preview_id_as_the_primary_contract(self):
        deployment = (ROOT / "DEPLOYMENT.md").read_text()

        self.assertIn("`preview_id`", deployment)
        self.assertIn("deprecated compatibility alias", deployment)
        self.assertNotIn("complete parameters plus `approval_token`", deployment)

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
        self.assertEqual(len(responses[1]["result"]["tools"]), 21)
        self.assertFalse(
            any(tool["name"].startswith("trade_") for tool in responses[1]["result"]["tools"])
        )
        self.assertNotIn("resultType", responses[1]["result"])

    def test_stdio_cli_honors_trade_enabled_environment_setting(self):
        requests = "\n".join(
            json.dumps(request)
            for request in (
                {
                    "jsonrpc": "2.0",
                    "id": "stdio-init",
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {},
                        "clientInfo": {"name": "stdio-test", "version": "1"},
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": "stdio-trade",
                    "method": "tools/call",
                    "params": {"name": "trade_preview_order", "arguments": {}},
                },
            )
        ) + "\n"
        environment = os.environ.copy()
        environment.update(
            {
                "PYTHONPATH": str(ROOT / "src"),
                "WEEX_TRADE_ENABLED": "true",
                "WEEX_APPROVAL_TOKEN_SECRET": "s" * 32,
            }
        )

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
        result = responses[-1]["result"]["structuredContent"]
        self.assertEqual(result["error"]["code"], "credential_missing")
        self.assertNotEqual(result["error"]["code"], "module_disabled")

    def test_stdio_cli_reports_invalid_environment_without_traceback(self):
        environment = os.environ.copy()
        environment.update(
            {
                "PYTHONPATH": str(ROOT / "src"),
                "WEEX_TRADE_ENABLED": "not-a-boolean",
            }
        )

        completed = subprocess.run(
            [sys.executable, "-m", "weex_mcp", "stdio"],
            input="",
            text=True,
            capture_output=True,
            timeout=5,
            cwd=ROOT,
            env=environment,
            check=False,
        )

        self.assertEqual(completed.returncode, 2)
        self.assertIn("WEEX_TRADE_ENABLED", completed.stderr)
        self.assertNotIn("Traceback", completed.stderr)


if __name__ == "__main__":
    unittest.main()
