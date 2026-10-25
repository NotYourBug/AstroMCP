from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as N

import pytest
from k8s_check_mcp import diagnostics as m

NOW = datetime.now(timezone.utc)


def pod(phase="Running", age=100, ready=True, container=None, init=None):
    return N(
        metadata=N(creation_timestamp=NOW - timedelta(seconds=age)),
        status=N(
            phase=phase,
            conditions=[
                N(
                    type="Ready",
                    status="True" if ready else "False",
                    last_transition_time=NOW - timedelta(seconds=age),
                )
            ],
            container_statuses=container or [],
            init_container_statuses=init or [],
        ),
    )


def test_init_crash_and_restart_detected(legacy_module):
    status = N(
        name="init-db",
        restart_count=8,
        state=N(waiting=N(reason="CrashLoopBackOff")),
        last_state=None,
    )
    value = pod(phase="Pending", init=[status])
    findings = m.evaluate_pod(value, thresholds=m.Thresholds(), now=NOW)
    assert any(
        f.code == "CrashLoopBackOff" and f.container == "init-db" and f.severity == "error"
        for f in findings
    )
    assert m._pod_restart_count(value) == 8
    assert m.select_problem_container(value, findings) == "init-db"


@pytest.mark.parametrize(
    "phase,age,code,severity",
    [
        ("Running", 61, "NotReady", "warning"),
        ("Running", 10, "Starting", "info"),
        ("Pending", 301, "Pending", "warning"),
        ("Pending", 10, "Starting", "info"),
    ],
)
def test_state_duration(legacy_module, phase, age, code, severity):
    findings = m.evaluate_pod(pod(phase, age, False), thresholds=m.Thresholds(), now=NOW)
    assert any(f.code == code and f.severity == severity for f in findings)


def test_normal_init_and_recovered_oom_not_current_failure(legacy_module):
    done = N(
        name="setup",
        restart_count=0,
        state=N(terminated=N(reason="Completed", exit_code=0)),
        last_state=None,
    )
    app = N(
        name="app",
        restart_count=1,
        ready=True,
        state=N(running=N()),
        last_state=N(terminated=N(reason="OOMKilled", exit_code=137)),
    )
    findings = m.evaluate_pod(pod(container=[app], init=[done]), thresholds=m.Thresholds(), now=NOW)
    assert not any(f.severity == "error" for f in findings)
    assert m.summarize_issue(findings) is None


def test_missing_status_is_safe(legacy_module):
    findings = m.evaluate_pod(N(status=None), thresholds=m.Thresholds(), now=NOW)
    assert findings and findings[0].code == "MissingStatus"
