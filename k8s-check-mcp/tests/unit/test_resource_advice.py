import pytest
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


@pytest.mark.parametrize("field", ["capacity", "allocatable"])
@pytest.mark.parametrize(
    "values,cpu,memory",
    [
        (None, None, None),
        ({}, None, None),
        ({"cpu": "4"}, 4000, None),
        ({"memory": "8Gi"}, None, 8 * 1024**3),
        ({"cpu": "0", "memory": "8Gi"}, None, 8 * 1024**3),
        ({"cpu": "bad", "memory": "8Gi"}, None, 8 * 1024**3),
        ({"cpu": "4", "memory": "0"}, 4000, None),
        ({"cpu": "4", "memory": "bad"}, 4000, None),
    ],
)
def test_audit_handles_unknown_capacity(node_pods, field, values, cpu, memory):
    inspector, _, nodes, _ = node_pods()
    setattr(nodes[0].status, field, values)
    result = inspector.get_node_resource_usage("node-000")
    assert result["ok"] and not result["complete"] and result["warnings"]
    assert result[field]["cpu_m"] == cpu
    assert result[field]["memory_bytes"] == memory
    if cpu is None:
        assert result[field]["cpu_human"] is None
    if memory is None:
        assert result[field]["memory_human"] is None
    assert result["requests"]["cpu_m"] == 5000
    assert result["requests"]["memory_bytes"] == 1024**3
    if field == "allocatable":
        assert result["overcommit"]["requests"] is None
        assert result["overcommit"]["limits"] is None
        assert result["resource_advice"] == []
        if cpu is None:
            assert result["requests"]["cpu_ratio"] is None
        if memory is None:
            assert result["requests"]["memory_ratio"] is None
    else:
        assert result["overcommit"]["requests"] is True


def test_audit_unsupported_resources_remain_unknown(node_pods):
    inspector, _, _, pods = node_pods()
    pods[0]._astromcp_raw = {
        "spec": {
            "containers": [{"name": "app", "resources": {"requests": {"cpu": "5"}}}],
            "resources": {"requests": {"cpu": "6"}},
        }
    }
    result = inspector.get_node_resource_usage("node-000")
    assert not result["complete"]
    assert result["overcommit"]["requests"] is None
