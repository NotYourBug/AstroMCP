from fastmcp import Client


async def test_host_package_factory_and_instances():
    from resource_check_mcp.config import load_settings
    from resource_check_mcp.server import create_server
    first=create_server(load_settings({"MCP_DISK_THRESHOLD":"70"}))
    second=create_server(load_settings({"MCP_DISK_THRESHOLD":"90"}))
    assert first is not second
    async with Client(first) as client:
        assert len(await client.list_tools())==6
        result=await client.call_tool("check_mem",{})
        assert result.data["ok"]
        assert result.data["complete"]
        assert "total_gb" in result.data
    async with Client(second) as client:
        assert len(await client.list_tools())==6


def test_pure_collectors_import_without_server():
    from resource_check_mcp.collectors.macos import parse_cpu_output
    assert parse_cpu_output("CPU usage: 12% user, 8% sys, 80% idle")==20
