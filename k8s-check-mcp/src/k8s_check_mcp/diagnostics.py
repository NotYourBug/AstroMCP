from __future__ import annotations

from datetime import datetime
from typing import Any

from .config import Thresholds
from .models import Finding, _container_statuses, _value
from .presentation import _get_conditions, _now_utc, _ratio, _safe_name, node_metric_view
from .quantities import _cpu_to_millicores, _memory_to_bytes

_BAD_POD_REASONS = {
    "CrashLoopBackOff",
    "ImagePullBackOff",
    "ErrImagePull",
    "CreateContainerConfigError",
    "CreateContainerError",
    "RunContainerError",
    "InvalidImageName",
    "OOMKilled",
}


def _age_seconds(timestamp, now):
    if isinstance(timestamp, str):
        try:
            timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
        return None
    age = (now - timestamp).total_seconds()
    return age if age >= 0 else None


def evaluate_pod(pod, *, thresholds: Thresholds, now: datetime) -> list[Finding]:
    status = _value(pod, "status")
    if status is None:
        return [Finding("MissingStatus", "warning")]
    phase = _value(status, "phase", "")
    findings = []
    if phase in {"Failed", "Unknown"}:
        findings.append(Finding("Pod" + phase, "error", evidence={"phase": phase}))
    for cs, kind in _container_statuses(pod):
        name = _value(cs, "name")
        state = _value(cs, "state")
        waiting = _value(state, "waiting")
        terminated = _value(state, "terminated")
        previous = _value(_value(cs, "last_state"), "terminated")
        reason = _value(waiting, "reason")
        if reason in _BAD_POD_REASONS:
            findings.append(Finding(reason, "error", name, {"state": "waiting", "kind": kind}))
        if terminated is not None and (
            _value(terminated, "exit_code", 0) or _value(terminated, "reason") in _BAD_POD_REASONS
        ):
            findings.append(
                Finding(
                    _value(terminated, "reason") or "ContainerTerminated",
                    "error",
                    name,
                    {
                        "state": "terminated",
                        "kind": kind,
                        "exit_code": _value(terminated, "exit_code"),
                    },
                )
            )
        if previous is not None and _value(previous, "reason") in _BAD_POD_REASONS:
            findings.append(
                Finding(
                    _value(previous, "reason"), "info", name, {"state": "historical", "kind": kind}
                )
            )
        restarts = _value(cs, "restart_count", 0) or 0
        if isinstance(restarts, int) and restarts >= thresholds.pod_restart_count:
            findings.append(
                Finding(
                    "RestartCount",
                    "warning",
                    name,
                    {"count": restarts, "threshold": thresholds.pod_restart_count, "kind": kind},
                )
            )
    created = _value(_value(pod, "metadata"), "creation_timestamp")
    if phase == "Pending":
        age = _age_seconds(created, now)
        severity = "warning" if age is None or age >= thresholds.pending_seconds else "info"
        findings.append(
            Finding(
                "Pending" if severity == "warning" else "Starting",
                severity,
                evidence={"age_seconds": age},
            )
        )
    elif phase == "Running":
        condition = next(
            (c for c in _value(status, "conditions") or [] if _value(c, "type") == "Ready"), None
        )
        unready = _value(condition, "status") == "False" or any(
            (_value(cs, "ready") is False for cs, kind in _container_statuses(pod) if kind == "app")
        )
        if unready:
            age = _age_seconds(
                _value(condition, "last_transition_time") if condition is not None else created, now
            )
            severity = "warning" if age is None or age >= thresholds.ready_seconds else "info"
            findings.append(
                Finding(
                    "NotReady" if severity == "warning" else "Starting",
                    severity,
                    evidence={"age_seconds": age},
                )
            )
    ranks = {"error": 0, "warning": 1, "info": 2}
    findings.sort(
        key=lambda finding: (
            ranks[finding.severity],
            0 if finding.code.startswith("Pod") else 1,
            finding.code,
            finding.container or "",
        )
    )
    return findings


def summarize_issue(findings):
    for finding in findings:
        if finding.severity == "info":
            continue
        if finding.code in {"PodFailed", "PodUnknown"}:
            return "phase=" + finding.evidence["phase"]
        state = finding.evidence.get("state")
        if state in {"waiting", "terminated"}:
            return state + "=" + finding.code
        if finding.code == "RestartCount":
            return "restart_count>=" + str(finding.evidence["threshold"])
        return finding.code
    return None


def select_problem_container(pod, findings):
    return next(
        (
            finding.container
            for finding in findings
            if finding.container and finding.severity != "info"
        ),
        None,
    )


def _pod_restart_count(pod):
    return sum((int(_value(cs, "restart_count", 0) or 0) for cs, _ in _container_statuses(pod)))


def _pod_issue(pod, restart_threshold):
    thresholds = Thresholds().model_copy(update={"pod_restart_count": max(restart_threshold, 1)})
    findings = evaluate_pod(pod, thresholds=thresholds, now=_now_utc())
    if restart_threshold <= 0:
        findings = [f for f in findings if f.code != "RestartCount"]
    return summarize_issue(findings)


def _pod_issue_summary(pod, restart_threshold, thresholds=None):
    thresholds = thresholds or Thresholds().model_copy(
        update={"pod_restart_count": max(restart_threshold, 1)}
    )
    return summarize_issue(evaluate_pod(pod, thresholds=thresholds, now=_now_utc()))


def _pod_suggestions(issue: str | None, phase: str, findings=None) -> list[str]:
    """把常见 issue/phase 映射成可操作的排障建议（给 LLM 作为输出参考）。"""
    if findings and any(f.code == "Starting" for f in findings):
        return ["Pod is still starting; inspect readiness and events before judging health."]
    if issue is None and phase == "Running":
        return ["Pod 当前未发现明显异常，可继续检查依赖服务、探针与业务日志。"]
    if issue is None and phase == "Pending":
        return ["重点查看调度失败事件、PVC 绑定状态、镜像拉取与节点资源余量。"]
    out: list[str] = []
    if issue and "CrashLoopBackOff" in issue:
        out.append("优先查看 previous 日志，确认应用启动异常、配置错误或依赖不可达。")
        out.append("同时检查探针、环境变量、Secret/ConfigMap 挂载是否正确。")
    if issue and ("ImagePullBackOff" in issue or "ErrImagePull" in issue):
        out.append("检查镜像地址、tag、镜像仓库凭据以及节点到仓库的网络连通性。")
    if issue and "OOMKilled" in issue:
        out.append("检查容器内存 limit/request 是否过低，以及应用是否存在内存泄漏。")
    if issue and "restart_count" in issue:
        out.append("容器重启偏多，建议结合事件与日志确认是否为瞬时抖动还是持续性故障。")
    if phase == "Pending":
        out.append("Pending 优先看调度事件，例如资源不足、污点不容忍、PVC 未绑定。")
    if not out:
        out.append("结合 Pod 事件、容器状态与最近日志进一步确认根因。")
    return out


def evaluate_node(node, snapshot, settings):
    conditions = _get_conditions(node)
    reasons: list[str] = []
    metric_detail: dict[str, Any] = {}
    node_name = _safe_name(node)
    if conditions.get("Ready") != "True":
        reasons.append("NodeNotReady")
    if conditions.get("DiskPressure") == "True":
        reasons.append("DiskPressure")
    if conditions.get("MemoryPressure") == "True":
        reasons.append("MemoryPressure")
    if conditions.get("PIDPressure") == "True":
        reasons.append("PIDPressure")
    if conditions.get("NetworkUnavailable") == "True":
        reasons.append("NetworkUnavailable")
    if bool(getattr(node.spec, "unschedulable", False)):
        reasons.append("Unschedulable")
    metrics = node_metric_view(node_name, snapshot)
    capacity = node.status.capacity if node.status else {}
    if metrics and capacity:
        cpu_cap_m = _cpu_to_millicores(str(capacity.get("cpu", "0")))
        mem_cap_b = _memory_to_bytes(str(capacity.get("memory", "0")))
        cpu_ratio = _ratio(metrics["cpu_usage_m"], cpu_cap_m)
        mem_ratio = _ratio(metrics["memory_usage_bytes"], mem_cap_b)
        metric_detail = {
            **metrics,
            "cpu_capacity_m": cpu_cap_m,
            "memory_capacity_bytes": mem_cap_b,
            "cpu_utilization": cpu_ratio,
            "memory_utilization": mem_ratio,
        }
        if cpu_ratio is not None and cpu_ratio >= settings.thresholds.node_cpu_utilization:
            reasons.append("HighCPUUsage")
        if mem_ratio is not None and mem_ratio >= settings.thresholds.node_memory_utilization:
            reasons.append("HighMemoryUsage")
    return (reasons, metric_detail)
