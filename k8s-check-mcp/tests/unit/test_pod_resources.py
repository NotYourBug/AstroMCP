import json
import time
from types import SimpleNamespace as N
from unittest.mock import Mock

import pytest
from k8s_check_mcp import resources as m
from k8s_check_mcp.client import K8sGateway
from k8s_check_mcp.config import load_settings


def container(cpu, sidecar=False, limit=None):
    return {
        "name": "test",
        "resources": {"requests": {"cpu": cpu}, "limits": {"cpu": limit} if limit else {}},
        **({"restartPolicy": "Always"} if sidecar else {}),
    }


@pytest.mark.parametrize(
    "inits,app,overhead,expected",
    [
        ([container("100m"), container("200m")], "150m", "0", 200),
        ([container("200m", True)], "500m", "100m", 800),
        (
            [container("100m", True), container("700m"), container("200m", True)],
            "500m",
            "100m",
            900,
        ),
        (
            [container("100m", True), container("200m", True), container("700m")],
            "500m",
            "100m",
            1100,
        ),
    ],
)
def test_effective_resources(legacy_module, inits, app, overhead, expected):
    pod = {
        "spec": {
            "containers": [container(app)],
            "initContainers": inits,
            "overhead": {"cpu": overhead},
        }
    }
    result = m.effective_resources(pod)
    assert result.cpu_request_m == expected
    assert result.cpu_limit_m is None
    assert result.complete


def test_pod_level_and_resize_are_explicitly_unsupported(legacy_module):
    pod = {"spec": {"containers": [container("500m")], "resources": {"requests": {"cpu": "1"}}}}
    result = m.effective_resources(pod)
    assert not result.complete and "UNSUPPORTED_RESOURCE_MODEL" in result.warnings


def test_raw_response_survives_sdk_field_loss(legacy_module):
    raw = {"spec": {"containers": [container("500m")], "resources": {"requests": {"cpu": "1"}}}}
    response = N(data=json.dumps(raw).encode(), release_conn=Mock())
    clients = N(api_client=Mock())
    clients.api_client.deserialize.return_value = N(
        spec=N(containers=[], init_containers=[], overhead={})
    )
    gateway = K8sGateway(load_settings({}), client_factory=lambda: clients)

    def read_namespaced_pod(**kwargs):
        return response

    result = gateway.call(read_namespaced_pod, deadline=time.monotonic() + 1)
    assert not m.effective_resources(result).complete
    response.release_conn.assert_called_once()
