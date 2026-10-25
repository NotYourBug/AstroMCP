import json
from pathlib import Path

import pytest
from fastmcp import Client

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("kind", ["host", "k8s"])
def test_example_configuration(kind):
    if kind == "host":
        from resource_check_mcp.config import load_settings
    else:
        from k8s_check_mcp.config import load_settings
    settings = load_settings({}, ROOT / "examples" / (kind + "-config.json"))
    assert settings.bind_host == "127.0.0.1"
    assert settings.transport == "stdio"
    assert settings.tool_timeout_seconds > settings.queue_timeout_seconds


async def test_documented_tools_exist(legacy_module):
    examples = json.loads((ROOT / "examples/tool-calls.json").read_text(encoding="utf-8"))
    for kind in ("host", "k8s"):
        async with Client(legacy_module(kind).mcp) as client:
            tools = {tool.name for tool in await client.list_tools()}
        assert {call["name"] for call in examples[kind]} <= tools


def test_client_commands_and_migration_documented():
    clients = json.loads((ROOT / "examples/mcp-clients.json").read_text(encoding="utf-8"))
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for kind in ("host", "k8s"):
        name = "astromcp-" + kind
        assert clients["mcpServers"][name]["args"][-1] == name
        assert name in readme
    compatibility = (ROOT / "docs/compatibility.md").read_text(encoding="utf-8")
    for term in ("known_hosts", "loopback", "continue_token", "coverage_scope", "node_count"):
        assert term in compatibility
