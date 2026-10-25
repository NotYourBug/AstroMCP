from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as N
from unittest.mock import Mock

import pytest
from k8s_check_mcp.client import K8sGateway
from k8s_check_mcp.config import K8sSettings, Thresholds
from k8s_check_mcp.diagnostics import evaluate_pod, summarize_issue
from k8s_check_mcp.inspector import K8sInspector
from k8s_check_mcp.resources import effective_resources


@pytest.mark.parametrize(
    "capacity", [{}, {"cpu": "0", "memory": "8Gi"}, {"cpu": "4", "memory": "0"}]
)
def test_missing_node_capacity_keeps_scan_incomplete(cluster, capacity):
    inspector, _, nodes, _ = cluster(1)
    nodes[0].status.capacity = capacity
    nodes[0].status.conditions.append(N(type="MemoryPressure", status="True"))
    result = inspector.cluster_summary()
    assert not result["complete"] and result["warnings"]
    assert result["problem_node_count"] is None
    assert result["observed_problem_node_count"] == 1


def test_unknown_readiness_propagates_coverage(cluster):
    inspector, gateway, _, _ = cluster(1)
    pod = N(
        metadata=N(name="app", namespace="default"),
        status=N(
            phase="Running",
            conditions=[],
            container_statuses=[],
            init_container_statuses=[],
            qos_class="BestEffort",
        ),
    )
    gateway.clients.core.list_pod_for_all_namespaces = lambda **kwargs: N(
        items=[pod], metadata=N(_continue=None)
    )
    result = inspector.list_node_pods("node-000")
    assert not result["complete"] and result["warnings"]
    assert result["items"][0]["issue"] == "ReadinessUnknown"


@pytest.mark.parametrize("condition", ["PodResizePending", "PodResizeInProgress"])
def test_active_resize_condition_is_unknown(condition):
    pod = {
        "spec": {"containers": [{"name": "app", "resources": {"requests": {"cpu": "1"}}}]},
        "status": {"conditions": [{"type": condition, "status": "True"}]},
    }
    assert not effective_resources(pod).complete


def test_starting_advice_does_not_hide_init_crash():
    from k8s_check_mcp.diagnostics import _pod_suggestions

    now = datetime.now(timezone.utc)
    pod = {
        "metadata": {"creationTimestamp": (now - timedelta(seconds=10)).isoformat()},
        "status": {
            "phase": "Pending",
            "initContainerStatuses": [
                {"name": "init", "state": {"waiting": {"reason": "CrashLoopBackOff"}}}
            ],
        },
    }
    findings = evaluate_pod(pod, thresholds=Thresholds(), now=now)
    suggestions = _pod_suggestions(summarize_issue(findings), "Pending", findings)
    assert any("日志" in text or "CrashLoop" in text for text in suggestions)
    assert not all("still starting" in text for text in suggestions)


def test_prompt_uses_request_contributors():
    from k8s_check_mcp.prompts import node_triage_prompt

    text = node_triage_prompt("test")
    assert "BestEffort Pod 迁移建议" not in text
    assert "贡献" in text or "contributor" in text


def test_overdue_api_response_is_not_healthy():
    clock = [0.0]

    def list_node(**kwargs):
        clock[0] = 0.15
        return N(items=[], metadata=N(_continue=None))

    settings = K8sSettings(tool_timeout_seconds=0.05, queue_timeout_seconds=0)
    gateway = K8sGateway(
        settings,
        clock=lambda: clock[0],
        client_factory=lambda: N(
            core=N(list_node=list_node), api_client=Mock(), display_name="test"
        ),
    )
    result = K8sInspector(settings, gateway).health_check()
    assert not result["ok"] and not result["complete"]
    assert result["error_code"] == "TIMEOUT"
    assert result["status"] == "unhealthy"


@pytest.mark.parametrize("ready", [None, "Unknown"])
def test_missing_readiness_is_not_healthy(ready):
    now = datetime.now(timezone.utc)
    pod = {
        "metadata": {"name": "test", "creationTimestamp": (now - timedelta(minutes=5)).isoformat()},
        "status": {
            "phase": "Running",
            "conditions": ([] if ready is None else [{"type": "Ready", "status": ready}]),
        },
    }
    findings = evaluate_pod(pod, thresholds=Thresholds(), now=now)
    assert any(f.code == "ReadinessUnknown" and f.severity == "warning" for f in findings)
    assert summarize_issue(findings) is not None


@pytest.mark.parametrize("field", ["requests", "limits"])
def test_resize_checks_status_resources_even_if_allocated_matches(field):
    declared = {"requests": {"cpu": "1"}, "limits": {"cpu": "2"}}
    actual = {"requests": {"cpu": "1"}, "limits": {"cpu": "2"}}
    actual[field] = {"cpu": "500m"}
    pod = {
        "spec": {"containers": [{"name": "app", "resources": declared}]},
        "status": {
            "containerStatuses": [
                {"name": "app", "allocatedResources": {"cpu": "1"}, "resources": actual}
            ]
        },
    }
    result = effective_resources(pod)
    assert not result.complete
    assert "UNSUPPORTED_RESOURCE_MODEL" in result.warnings
