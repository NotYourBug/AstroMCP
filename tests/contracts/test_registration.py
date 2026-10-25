from fastmcp import Client

EXPECTED = {
    "host": {
        "check_host_status",
        "check_disk_usage",
        "check_mem",
        "check_cpu",
        "check_tcp",
        "check_service_port",
    },
    "k8s": {
        "list_nodes",
        "get_node_detail",
        "get_node_resource_usage",
        "find_problem_nodes",
        "list_node_pods",
        "diagnose_pod_issues",
        "health_check",
    },
}


async def test_registration(legacy_module):
    for kind, names in EXPECTED.items():
        async with Client(legacy_module(kind).mcp) as client:
            assert {t.name for t in await client.list_tools()} == names
            if kind == "k8s":
                assert {str(r.uri) for r in await client.list_resources()} == {
                    "k8s://cluster/summary"
                }
                assert {p.name for p in await client.list_prompts()} == {
                    "node_triage_prompt",
                    "cluster_problem_scan_prompt",
                }
