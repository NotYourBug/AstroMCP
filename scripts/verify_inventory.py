"""Read MCP metadata through the real in-memory protocol client."""
from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_server_module(kind):
    if kind == "host":
        from types import SimpleNamespace
        from resource_check_mcp.server import create_server
        return SimpleNamespace(mcp=create_server())
    paths = {"host": "resource-check-mcp/resource-check-mcp.py", "k8s": "k8s-check-mcp/mcp_server.py"}
    name = f"baseline_{kind}"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, ROOT / paths[kind])
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


async def read_inventory(server):
    from fastmcp import Client
    async with Client(server) as client:
        return {tool.name: tool.inputSchema for tool in await client.list_tools()}


async def main():
    inventory = {kind: await read_inventory(load_server_module(kind).mcp) for kind in ("host", "k8s")}
    out = ROOT / "tests/contracts/baseline_inventory.json"
    out.write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"MCP inventory: {sum(len(v) for v in inventory.values())} tools")


if __name__ == "__main__":
    asyncio.run(main())
