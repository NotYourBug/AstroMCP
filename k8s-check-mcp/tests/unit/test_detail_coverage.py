from types import SimpleNamespace as N

import pytest
from kubernetes.client.exceptions import ApiException


@pytest.mark.parametrize("count", [20, 21])
def test_pod_sample_boundary(node_pods, count):
    inspector, _, _, _ = node_pods(count, cpu="100m", memory="1Mi")
    result = inspector.get_node_detail("node-000")
    assert result["pod_count"] == count
    assert result["pods_sample_returned"] == len(result["pods_sample"]) == 20
    assert result["pods_sample_truncated"] is (count > 20)
    assert result["complete"] is (count == 20)
    if count > 20:
        assert result["warnings"]


@pytest.mark.parametrize("count", [50, 51])
def test_resource_breakdown_boundary(node_pods, count):
    inspector, _, _, _ = node_pods(count, cpu="100m", memory="1Mi")
    result = inspector.get_node_resource_usage("node-000")
    assert result["pod_count"] == count
    assert result["pod_breakdown_returned"] == len(result["pod_breakdown"]) == 50
    assert result["pod_breakdown_truncated"] is (count > 50)
    assert result["requests"]["cpu_m"] == count * 100
    assert result["requests"]["memory_bytes"] == count * 1024**2
    assert result["complete"] is (count == 50)


def test_truncation_preserves_known_overcommit(node_pods):
    inspector, _, _, _ = node_pods(51, cpu="100m", memory="1Mi")
    result = inspector.get_node_resource_usage("node-000")
    assert result["pod_breakdown_truncated"] and not result["complete"]
    assert result["overcommit"]["requests"] is True
    assert result["resource_advice"]


@pytest.mark.parametrize("audit", [False, True])
def test_partial_api_scan_does_not_claim_total(node_pods, audit):
    inspector, gateway, _, pods = node_pods(2, cpu="100m")

    def partial(**kwargs):
        if kwargs["_continue"]:
            raise ApiException(status=403)
        return N(items=pods, metadata=N(_continue="TEST_ONLY_NEXT"))

    gateway.clients.core.list_pod_for_all_namespaces = partial
    result = (inspector.get_node_resource_usage if audit else inspector.get_node_detail)("node-000")
    assert not result["complete"] and result["warnings"]
    key = "pod_breakdown" if audit else "pods_sample"
    assert result[f"{key}_returned"] == len(result[key]) == 2
    assert result[f"{key}_truncated"] is False
    if audit:
        assert result["overcommit"]["requests"] is None
