import json
from pathlib import Path

from fastmcp import Client


async def test_original_parameters(legacy_module):
    baseline = json.loads((Path(__file__).parent / "baseline_inventory.json").read_text(encoding="utf-8"))
    for kind in ("host", "k8s"):
        async with Client(legacy_module(kind).mcp) as client:
            tools = {t.name: t.inputSchema for t in await client.list_tools()}
        for name, expected in baseline[kind].items():
            schema = tools[name]
            assert set(expected["properties"]) <= set(schema["properties"])
            assert schema.get("required", []) == expected.get("required", [])
            for key, prop in expected["properties"].items():
                assert schema["properties"][key].get("default") == prop.get("default")
