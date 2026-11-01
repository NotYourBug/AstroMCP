import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as N

import pytest
from k8s_check_mcp.client import read_events, read_log_excerpt


def test_json_authorization_redacted_in_log_and_event(cluster):
    _, gateway, _, _ = cluster()
    secret = "TEST_ONLY_BEARER_TOKEN"
    text = '{"Authorization": "Bearer ' + secret + '"}'
    gateway.clients.core.read_namespaced_pod_log = lambda **kwargs: text
    event = N(
        involved_object=N(uid="pod"),
        metadata=N(name="e"),
        message=text,
        reason="Test",
        type="Warning",
        last_timestamp=datetime.now(timezone.utc),
    )
    gateway.clients.core.list_namespaced_event = lambda **kwargs: N(
        items=[event], metadata=N(_continue=None)
    )
    logs = read_log_excerpt(
        gateway,
        namespace="default",
        pod_name="pod",
        container_name="app",
        deadline=time.monotonic() + 1,
    )
    events = read_events(gateway, namespace="default", pod_uid="pod", deadline=time.monotonic() + 1)
    assert secret not in logs.data
    assert secret not in str(events.data)


def test_events_use_uid_and_latest_twenty(cluster):
    m, gateway, _, _ = cluster()
    now = datetime.now(timezone.utc)
    events = [
        N(
            involved_object=N(uid="new"),
            reason="Test",
            type="Warning",
            message="event",
            last_timestamp=now + timedelta(seconds=i),
            event_time=None,
            metadata=N(name=str(i)),
        )
        for i in range(25)
    ]
    events.append(
        N(
            involved_object=N(uid="old"),
            reason="Old",
            type="Warning",
            message="stale",
            last_timestamp=now + timedelta(days=1),
            event_time=None,
            metadata=N(name="old"),
        )
    )
    calls = []

    def list_events(**kwargs):
        calls.append(kwargs)
        return N(items=events, metadata=N(_continue=None))

    gateway.clients.core.list_namespaced_event = list_events
    result = read_events(gateway, namespace="default", pod_uid="new", deadline=time.monotonic() + 1)
    assert "involvedObject.uid=new" in calls[0]["field_selector"]
    assert len(result.data) == 20 and result.data[0]["reason"] == "Test"
    assert result.data[0]["last_timestamp"] == str(now + timedelta(seconds=24))


def test_log_budget_and_redaction(cluster):
    m, gateway, _, _ = cluster()
    calls = []

    def read_log(**kwargs):
        calls.append(kwargs)
        return "password=TEST_SECRET\n" + "x" * 20000

    gateway.clients.core.read_namespaced_pod_log = read_log
    result = read_log_excerpt(
        gateway,
        namespace="default",
        pod_name="pod",
        container_name="init",
        deadline=time.monotonic() + 1,
    )
    assert calls[0]["limit_bytes"] == 16384
    assert len(result.data) <= 4000 and "TEST_SECRET" not in result.data
    assert not result.complete


def test_permission_failure_returns_partial_evidence(cluster):
    from kubernetes.client.exceptions import ApiException

    m, gateway, _, _ = cluster()

    def denied(**kwargs):
        raise ApiException(status=403)

    gateway.clients.core.list_namespaced_event = denied
    result = read_events(gateway, namespace="default", pod_uid="uid", deadline=time.monotonic() + 1)
    assert not result.complete and result.data == [] and result.warnings


@pytest.fixture
def diagnostic_case(node_pods):
    from kubernetes import client

    inspector, gateway, _, pods = node_pods()
    pod = pods[0]
    pod.status.phase = "Failed"
    pod.status.container_statuses = [
        client.V1ContainerStatus(
            name="app",
            image="TEST_ONLY",
            image_id="TEST_ONLY",
            ready=False,
            restart_count=0,
            state=client.V1ContainerState(
                terminated=client.V1ContainerStateTerminated(exit_code=137, reason="OOMKilled")
            ),
        )
    ]
    gateway.clients.core.read_namespaced_pod = lambda **kwargs: pod
    gateway.clients.core.list_namespaced_event = lambda **kwargs: N(
        items=[], metadata=N(_continue=None)
    )
    calls = []

    def log(**kwargs):
        calls.append(kwargs)
        return "TEST_ONLY_OOM_EVIDENCE"

    gateway.clients.core.read_namespaced_pod_log = log
    return inspector, gateway, pod, calls


def test_failed_oom_collects_current_logs(diagnostic_case):
    inspector, _, pod, calls = diagnostic_case
    result = inspector.diagnose_pod_issues(pod.metadata.name, "default")
    assert result["issue"] == "phase=Failed"
    assert result["problem_container"] == "app"
    assert "TEST_ONLY_OOM_EVIDENCE" in result["log_excerpt"]
    assert calls[0]["previous"] is False
    assert calls[0]["limit_bytes"] == 16384


def test_crashloop_prefers_previous_logs(diagnostic_case):
    from kubernetes import client

    inspector, _, pod, calls = diagnostic_case
    pod.status.phase = "Running"
    status = pod.status.container_statuses[0]
    status.restart_count = 3
    status.state = client.V1ContainerState(
        waiting=client.V1ContainerStateWaiting(reason="CrashLoopBackOff")
    )
    result = inspector.diagnose_pod_issues(pod.metadata.name, "default")
    assert result["log_excerpt"] and calls[0]["previous"] is True


def test_recovered_oom_does_not_collect_logs(diagnostic_case):
    from kubernetes import client

    inspector, _, pod, calls = diagnostic_case
    pod.status.phase = "Running"
    status = pod.status.container_statuses[0]
    status.last_state = status.state
    status.state = client.V1ContainerState(running=client.V1ContainerStateRunning())
    status.restart_count = 1
    status.ready = True
    result = inspector.diagnose_pod_issues(pod.metadata.name, "default")
    assert result["issue"] is None and calls == []


@pytest.mark.parametrize("disabled", [False, True])
def test_failed_oom_log_denied_retains_findings(diagnostic_case, disabled):
    from kubernetes.client.exceptions import ApiException

    inspector, gateway, pod, calls = diagnostic_case
    if disabled:
        gateway.settings = gateway.settings.model_copy(update={"include_logs": False})

    def denied(**kwargs):
        calls.append(kwargs)
        raise ApiException(status=403)

    gateway.clients.core.read_namespaced_pod_log = denied
    result = inspector.diagnose_pod_issues(pod.metadata.name, "default")
    assert not result["complete"] and result["warnings"]
    assert any(f["code"] == "OOMKilled" for f in result["findings"])
    assert len(calls) == (0 if disabled else 1)


def test_current_log_failure_falls_back_to_previous(diagnostic_case):
    from kubernetes.client.exceptions import ApiException

    inspector, gateway, pod, calls = diagnostic_case

    def fallback(**kwargs):
        calls.append(kwargs)
        if not kwargs["previous"]:
            raise ApiException(status=404)
        return "TEST_ONLY_PREVIOUS"

    gateway.clients.core.read_namespaced_pod_log = fallback
    result = inspector.diagnose_pod_issues(pod.metadata.name, "default")
    assert [call["previous"] for call in calls] == [False, True]
    assert result["log_excerpt"] == "TEST_ONLY_PREVIOUS"
    assert not result["complete"] and result["warnings"]
