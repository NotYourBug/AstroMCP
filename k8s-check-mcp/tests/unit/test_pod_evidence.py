import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as N

from k8s_check_mcp.client import read_events, read_log_excerpt


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
