"""Check current MCP signatures without overwriting the original baseline."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


async def read_inventory(server):
    from fastmcp import Client

    async with Client(server) as client:
        return {tool.name: tool.inputSchema for tool in await client.list_tools()}


async def verify(output=None):
    from k8s_check_mcp.server import create_server as k8s_server
    from resource_check_mcp.server import create_server as host_server

    inventory = {
        kind: await read_inventory(factory())
        for kind, factory in (("host", host_server), ("k8s", k8s_server))
    }
    baseline_path = ROOT / "tests/contracts/baseline_inventory.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    for kind, expected_tools in baseline.items():
        assert set(inventory[kind]) == set(expected_tools), f"{kind}: tool set changed"
        for name, expected in expected_tools.items():
            actual = inventory[kind][name]
            assert actual.get("required", []) == expected.get("required", []), name
            for key, prop in expected["properties"].items():
                assert key in actual["properties"], f"{name}.{key} missing"
                assert actual["properties"][key].get("default") == prop.get("default"), (
                    f"{name}.{key} default"
                )
    if output:
        if output.resolve() == baseline_path.resolve():
            raise ValueError("Original baseline is immutable; choose another output path")
        output.write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"MCP inventory: PASS ({sum(len(v) for v in inventory.values())} tools)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, help="Optional current snapshot; not the original baseline"
    )
    args = parser.parse_args()
    asyncio.run(verify(args.output))


if __name__ == "__main__":
    main()
