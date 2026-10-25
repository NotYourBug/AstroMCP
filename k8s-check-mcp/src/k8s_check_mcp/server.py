from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastmcp import FastMCP

from .client import K8sGateway
from .config import K8sSettings, load_settings
from .inspector import K8sInspector
from .prompts import cluster_problem_scan_prompt, node_triage_prompt


def create_server(
    settings: K8sSettings | None = None, *, gateway: K8sGateway | None = None
) -> FastMCP:
    settings = settings or load_settings(os.environ)
    inspector = K8sInspector(settings, gateway)

    @asynccontextmanager
    async def lifespan(server):
        try:
            yield {}
        finally:
            inspector.gateway.close()

    mcp = FastMCP("K8s Check MCP", lifespan=lifespan)
    for name in (
        "list_nodes",
        "get_node_detail",
        "get_node_resource_usage",
        "find_problem_nodes",
        "list_node_pods",
        "diagnose_pod_issues",
        "health_check",
    ):
        mcp.tool(
            name=name,
            annotations={"readOnlyHint": True, "idempotentHint": True, "openWorldHint": True},
        )(getattr(inspector, name))
    mcp.resource("k8s://cluster/summary")(inspector.cluster_summary)
    mcp.prompt(name="node_triage_prompt")(node_triage_prompt)
    mcp.prompt(name="cluster_problem_scan_prompt")(cluster_problem_scan_prompt)
    return mcp
