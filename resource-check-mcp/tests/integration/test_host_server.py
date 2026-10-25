from fastmcp import Client


async def test_host_package_factory_and_instances():
    from resource_check_mcp.config import load_settings
    from resource_check_mcp.server import create_server

    first = create_server(load_settings({"MCP_DISK_THRESHOLD": "70"}))
    second = create_server(load_settings({"MCP_DISK_THRESHOLD": "90"}))
    assert first is not second
    async with Client(first) as client:
        assert len(await client.list_tools()) == 6
        result = await client.call_tool("check_mem", {})
        assert result.data["ok"]
        assert result.data["complete"]
        assert "total_gb" in result.data
    async with Client(second) as client:
        assert len(await client.list_tools()) == 6


def test_pure_collectors_import_without_server():
    from resource_check_mcp.collectors.macos import parse_cpu_output

    assert parse_cpu_output("CPU usage: 12% user, 8% sys, 80% idle") == 20


async def test_configured_disk_threshold_and_explicit_override(monkeypatch):
    from types import SimpleNamespace

    import psutil
    from resource_check_mcp.config import load_settings
    from resource_check_mcp.server import create_server

    monkeypatch.setattr(
        psutil, "disk_usage", lambda path: SimpleNamespace(total=100, used=80, free=20, percent=80)
    )
    async with Client(create_server(load_settings({"MCP_DISK_THRESHOLD": "70"}))) as client:
        tool = next(t for t in await client.list_tools() if t.name == "check_disk_usage")
        assert tool.inputSchema["properties"]["threshold"]["default"] == 70
        result = await client.call_tool("check_disk_usage", {})
        assert result.data["threshold"] == 70
        explicit = await client.call_tool("check_disk_usage", {"threshold": 85})
        assert explicit.data["threshold"] == 85
