from datetime import datetime, timezone
from types import SimpleNamespace as N
from unittest.mock import Mock

import pytest


@pytest.fixture
def cluster(legacy_module, monkeypatch):
    def make(count=5, metrics_error=None):
        from k8s_check_mcp.client import K8sGateway
        from k8s_check_mcp.config import load_settings
        from k8s_check_mcp.inspector import K8sInspector

        nodes = [
            N(
                metadata=N(name=f"node-{i:03}", creation_timestamp=datetime.now(timezone.utc)),
                spec=N(unschedulable=False, taints=[]),
                status=N(
                    conditions=[N(type="Ready", status="True")],
                    addresses=[],
                    node_info=None,
                    capacity={"cpu": "4", "memory": "8Gi"},
                    allocatable={"cpu": "4", "memory": "8Gi"},
                ),
            )
            for i in range(count)
        ]
        calls = {"metrics": 0, "list": []}

        def metrics(**kwargs):
            calls["metrics"] += 1
            if metrics_error:
                raise metrics_error
            return {
                "items": [
                    {
                        "metadata": {"name": n.metadata.name},
                        "usage": {"cpu": "100m", "memory": "100Mi"},
                    }
                    for n in nodes
                ]
            }

        def list_node(**kwargs):
            calls["list"].append(kwargs)
            return N(items=nodes, metadata=N(_continue=None))

        clients = N(
            display_name="test-cluster",
            api_client=Mock(),
            core=N(list_node=list_node),
            custom=N(list_cluster_custom_object=metrics),
        )
        gateway = K8sGateway(
            load_settings({"MCP_K8S_RATE_LIMIT_ENABLED": "false"}), client_factory=lambda: clients
        )
        m = K8sInspector(gateway.settings, gateway)
        return m, gateway, nodes, calls

    return make


@pytest.fixture
def node_pods(cluster):
    def make(count=1, cpu="5", memory="1Gi"):
        from kubernetes import client

        inspector, gateway, nodes, _ = cluster(1)
        pods = [
            client.V1Pod(
                metadata=client.V1ObjectMeta(name=f"app-{i}", namespace="default", uid=f"uid-{i}"),
                spec=client.V1PodSpec(
                    containers=[
                        client.V1Container(
                            name="app",
                            resources=client.V1ResourceRequirements(
                                requests={"cpu": cpu, "memory": memory}
                            ),
                        )
                    ]
                ),
                status=client.V1PodStatus(
                    phase="Running",
                    qos_class="Burstable",
                    conditions=[client.V1PodCondition(type="Ready", status="True")],
                ),
            )
            for i in range(count)
        ]
        gateway.clients.core.read_node = lambda *args, **kwargs: nodes[0]
        gateway.clients.core.list_pod_for_all_namespaces = lambda **kwargs: N(
            items=pods, metadata=N(_continue=None)
        )
        return inspector, gateway, nodes, pods

    return make
