"""Exercise the installed SDK at its HTTP boundary, including real deserialization."""

import json
import time
from types import SimpleNamespace as N
from unittest.mock import Mock

import pytest
from k8s_check_mcp.client import K8sGateway
from k8s_check_mcp.config import K8sSettings
from k8s_check_mcp.models import K8sOperationError
from k8s_check_mcp.resources import effective_resources
from kubernetes import client
from kubernetes.client.rest import RESTResponse
from urllib3 import HTTPResponse


@pytest.mark.parametrize("collection", [False, True])
def test_installed_sdk_preserves_raw_pods(monkeypatch, collection):
    pod = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": "sample", "namespace": "default"},
        "spec": {
            "containers": [
                {"name": "app", "image": "test-only", "resources": {"requests": {"cpu": "100m"}}}
            ],
            "resources": {"requests": {"cpu": "1"}},
        },
    }
    payload = (
        {
            "apiVersion": "v1",
            "kind": "PodList",
            "metadata": {"continue": "next-page"},
            "items": [pod],
        }
        if collection
        else pod
    )
    response = HTTPResponse(
        body=json.dumps(payload).encode(), status=200, headers={"Content-Type": "application/json"}
    )
    release = Mock(wraps=response.release_conn)
    monkeypatch.setattr(response, "release_conn", release)
    api = client.ApiClient(client.Configuration())
    monkeypatch.setattr(api.rest_client, "request", lambda *args, **kwargs: RESTResponse(response))
    core = client.CoreV1Api(api)
    gateway = K8sGateway(K8sSettings(), client_factory=lambda: N(api_client=api, core=core))
    try:
        if collection:
            result = gateway.call(
                core.list_pod_for_all_namespaces, deadline=time.monotonic() + 2, limit=100
            )
            assert result.metadata._continue == "next-page"
            result = result.items[0]
        else:
            result = gateway.call(
                core.read_namespaced_pod, "sample", "default", deadline=time.monotonic() + 2
            )
        assert result.metadata.name == "sample"
        assert result._astromcp_raw == pod
        assert not effective_resources(result).complete
        release.assert_called()
    finally:
        gateway.close()


@pytest.mark.parametrize(
    "status,code", [(401, "AUTH_FAILED"), (403, "PERMISSION_DENIED"), (404, "NOT_FOUND")]
)
def test_installed_sdk_raw_http_errors(monkeypatch, status, code):
    response = HTTPResponse(
        body=b'{"kind":"Status","message":"TEST_ONLY"}',
        status=status,
        headers={"Content-Type": "application/json"},
    )
    api = client.ApiClient(client.Configuration())
    monkeypatch.setattr(api.rest_client, "request", lambda *args, **kwargs: RESTResponse(response))
    core = client.CoreV1Api(api)
    gateway = K8sGateway(K8sSettings(), client_factory=lambda: N(api_client=api, core=core))
    try:
        with pytest.raises(K8sOperationError) as error:
            gateway.call(
                core.read_namespaced_pod, "sample", "default", deadline=time.monotonic() + 2
            )
        assert error.value.code == code
    finally:
        gateway.close()
