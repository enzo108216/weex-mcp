from __future__ import annotations

import hashlib
import json
from typing import Any

MANIFEST_VERSION = 1
TOOL_CONTRACT_VERSION = "1"
_RUNTIME_PROFILES = frozenset({"default", "local_user", "broker_server"})


def build_compatibility_manifest(
    *,
    package_version: str,
    runtime_profile: str,
    tools: list[dict[str, Any]],
) -> dict[str, Any]:
    if not isinstance(package_version, str) or not package_version.strip():
        raise ValueError("package_version must be a non-empty string.")
    if runtime_profile not in _RUNTIME_PROFILES:
        raise ValueError("runtime_profile must be default, local_user, or broker_server.")
    if not isinstance(tools, list):
        raise ValueError("tools must be a list.")

    names: list[str] = []
    schemas: list[dict[str, Any]] = []
    modules: set[str] = set()
    credential_modes: set[str] = set()
    delegated_authorization = False
    seen_names: set[str] = set()

    for tool in tools:
        if not isinstance(tool, dict):
            raise ValueError("Each tool must be an object.")
        name = tool.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("Each tool must have a non-empty name.")
        if name in seen_names:
            raise ValueError(f"Tool catalog contains duplicate tool name: {name}.")
        seen_names.add(name)

        input_schema = tool.get("inputSchema")
        output_schema = tool.get("outputSchema")
        if not isinstance(input_schema, dict) or not isinstance(output_schema, dict):
            raise ValueError(f"Tool {name} must declare inputSchema and outputSchema objects.")
        names.append(name)
        schemas.append(
            {
                "name": name,
                "inputSchema": input_schema,
                "outputSchema": output_schema,
            }
        )

        metadata = tool.get("_meta")
        weex_metadata = metadata.get("com.weex/mcp") if isinstance(metadata, dict) else None
        module = weex_metadata.get("module") if isinstance(weex_metadata, dict) else None
        if isinstance(module, str) and module:
            modules.add(module)

        properties = input_schema.get("properties")
        if not isinstance(properties, dict):
            properties = {}
        delegated_authorization = delegated_authorization or "delegated_authorization" in properties
        credential_schema = properties.get("credential")
        if isinstance(credential_schema, dict):
            credential_modes.update(_credential_modes(credential_schema))

    names.sort()
    schemas.sort(key=lambda item: item["name"])
    effective_trade_enabled = "trade" in modules
    _validate_runtime_profile(
        runtime_profile=runtime_profile,
        modules=modules,
        credential_modes=credential_modes,
        delegated_authorization=delegated_authorization,
        trade_enabled=effective_trade_enabled,
    )
    manifest: dict[str, Any] = {
        "manifest_version": MANIFEST_VERSION,
        "package_version": package_version,
        "tool_contract_version": TOOL_CONTRACT_VERSION,
        "runtime_profile": runtime_profile,
        "trade_enabled": effective_trade_enabled,
        "tools": names,
        "credential_modes": sorted(credential_modes),
        "delegated_authorization": delegated_authorization,
        "modules": sorted(modules),
        "tool_set_digest": _digest(names),
        "schema_digest": _digest(schemas),
    }
    manifest["manifest_digest"] = _digest(manifest)
    return manifest


def _validate_runtime_profile(
    *,
    runtime_profile: str,
    modules: set[str],
    credential_modes: set[str],
    delegated_authorization: bool,
    trade_enabled: bool,
) -> None:
    if runtime_profile == "default":
        return
    base_modules = {
        "local_user": {"account", "market", "partner"},
        "broker_server": {"account", "broker", "market"},
    }[runtime_profile]
    expected_modules = base_modules | ({"trade"} if trade_enabled else set())
    expected_modes = {"credential_ref"} if runtime_profile == "local_user" else {"plaintext"}
    expected_delegated = trade_enabled if runtime_profile == "local_user" else False
    if modules != expected_modules:
        raise ValueError(f"{runtime_profile} runtime profile modules do not match the effective tools.")
    if credential_modes != expected_modes:
        raise ValueError(f"{runtime_profile} runtime profile credential modes do not match the effective schemas.")
    if delegated_authorization is not expected_delegated:
        raise ValueError(f"{runtime_profile} runtime profile delegated authorization does not match the effective schemas.")


def _credential_modes(schema: dict[str, Any]) -> set[str]:
    modes: set[str] = set()
    candidates = [schema]
    one_of = schema.get("oneOf")
    if isinstance(one_of, list):
        candidates.extend(option for option in one_of if isinstance(option, dict))
    for candidate in candidates:
        properties = candidate.get("properties")
        type_schema = properties.get("type") if isinstance(properties, dict) else None
        mode = type_schema.get("const") if isinstance(type_schema, dict) else None
        if isinstance(mode, str) and mode:
            modes.add(mode)
    return modes


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"
