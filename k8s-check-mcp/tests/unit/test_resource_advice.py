from k8s_check_mcp import resources as m


def test_no_best_effort_eviction_advice(legacy_module):
    advice = m.resource_advice(
        [
            {"name": "heavy", "cpu_request_m": 2000, "memory_request_bytes": 0},
            {"name": "besteffort", "cpu_request_m": 0, "memory_request_bytes": 0},
        ],
        {"requests_overcommit": True},
    )
    assert "heavy" in " ".join(advice)
    assert "besteffort" not in " ".join(advice)


def test_audit_preserves_known_requests_when_metrics_unavailable(cluster):
    from types import SimpleNamespace as N

    from kubernetes.client.exceptions import ApiException

    m, gateway, nodes, _ = cluster(metrics_error=ApiException(status=404))
    container = N(
        name="app",
        resources=N(requests={"cpu": "5", "memory": "1Gi"}, limits={}),
        restart_policy=None,
    )
    pod = N(
        metadata=N(name="heavy", namespace="default"),
        spec=N(containers=[container], init_containers=[], overhead={}),
        status=N(
            phase="Running",
            qos_class="Burstable",
            container_statuses=[],
            init_container_statuses=[],
        ),
    )
    gateway.clients.core.read_node = lambda *args, **kwargs: nodes[0]
    gateway.clients.core.list_pod_for_all_namespaces = lambda **kwargs: N(
        items=[pod], metadata=N(_continue=None)
    )
    result = m.get_node_resource_usage("node-000")
    assert result["overcommit"]["requests"] is True
    assert not result["complete"]
    assert result["eviction_suggestion"] is None
