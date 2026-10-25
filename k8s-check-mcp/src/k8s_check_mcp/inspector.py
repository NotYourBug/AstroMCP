from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import datetime, timezone
from functools import wraps
from typing import Annotated, Any
from uuid import uuid4

from kubernetes import config
from pydantic import Field

from .client import K8sGateway, _page_parts, read_events, read_log_excerpt
from .diagnostics import (
    _pod_restart_count,
    _pod_suggestions,
    evaluate_node,
    evaluate_pod,
    node_capacity,
    select_problem_container,
    summarize_issue,
)
from .models import (
    _COVERAGE,
    _DEADLINE,
    EvidenceResult,
    K8sOperationError,
    _mark_incomplete,
    _value,
)
from .presentation import (
    _format_age,
    _format_bytes,
    _format_cpu_m,
    _get_conditions,
    _get_node_addresses,
    _now_utc,
    _parse_node,
    _pod_qos_class,
    _ratio,
    _safe_name,
    node_metric_view,
    pod_basic_view,
)
from .quantities import _cpu_to_millicores, _memory_to_bytes
from .resources import _pod_effective_resources, resource_advice

logger = logging.getLogger("astromcp.k8s")


def _operation(fn):

    @wraps(fn)
    def wrapped(self, *args, **kwargs):
        gateway = self.gateway
        token = _DEADLINE.set(gateway.clock() + gateway.settings.tool_timeout_seconds)
        coverage = {"complete": True, "warnings": []}
        state = _COVERAGE.set(coverage)
        request_id = str(uuid4())
        try:
            try:
                result = fn(self, *args, **kwargs)
            except K8sOperationError as exc:
                result = {"ok": False, "error": str(exc), "error_code": exc.code, "complete": False}
            except config.ConfigException:
                result = {
                    "ok": False,
                    "error": "Kubernetes configuration is unavailable",
                    "error_code": "CONFIG_ERROR",
                    "complete": False,
                }
            if gateway.clock() >= _DEADLINE.get():
                _mark_incomplete("TIMEOUT: tool deadline exceeded")
                if fn.__name__ == "health_check":
                    result.update(
                        ok=False, error_code="TIMEOUT", error="Connectivity deadline exceeded"
                    )
            result.setdefault("ok", "error" not in result)
            if fn.__name__ == "health_check" and (not result["ok"]):
                result["status"] = "unhealthy"
            result["complete"] = result.get("complete", True) and coverage["complete"]
            result["warnings"] = list(
                dict.fromkeys(result.get("warnings", []) + coverage["warnings"])
            )
            result.update(request_id=request_id, timestamp=datetime.now(timezone.utc).isoformat())
            logger.info(
                "tool=%s request_id=%s complete=%s", fn.__name__, request_id, result["complete"]
            )
            return result
        finally:
            _DEADLINE.reset(token)
            _COVERAGE.reset(state)

    return wrapped


def _validate_page_size(limit):
    if not isinstance(limit, int) or isinstance(limit, bool) or (not 1 <= limit <= 200):
        raise K8sOperationError("INVALID_INPUT", "Page/result limit must be between 1 and 200")


class K8sInspector:
    def __init__(self, settings, gateway=None):
        self.settings = settings
        self.gateway = gateway or K8sGateway(settings)

    def call(self, fn, *args, **kwargs):
        return self.gateway.call(
            fn,
            *args,
            deadline=_DEADLINE.get() or self.gateway.clock() + self.settings.tool_timeout_seconds,
            **kwargs,
        )

    def node_metric_view(self, custom, node_name, snapshot=None):
        result = node_metric_view(
            node_name, snapshot if snapshot is not None else self.metrics_snapshot()
        )
        if result is None:
            _mark_incomplete("Node usage not checked: metrics missing or invalid")
        return result

    def scan_snapshot(self, nodes):
        snapshot = self.metrics_snapshot()
        if any(node_metric_view(_safe_name(node), snapshot) is None for node in nodes):
            snapshot.complete = False
            _mark_incomplete("Node usage not checked: metrics missing or invalid")
        if any(None in node_capacity(node) for node in nodes):
            snapshot.complete = False
            _mark_incomplete("Node utilization not checked: capacity missing, zero or invalid")
        return snapshot

    def pod_findings(self, pod, thresholds=None):
        findings = evaluate_pod(
            pod, thresholds=thresholds or self.settings.thresholds, now=_now_utc()
        )
        if any(f.code in {"MissingStatus", "ReadinessUnknown"} for f in findings):
            _mark_incomplete("Pod status/readiness evidence is missing or unknown")
        return findings

    def pod_basic_view(self, pod, thresholds=None):
        findings = self.pod_findings(pod, thresholds)
        return pod_basic_view(pod, findings, summarize_issue(findings), _pod_restart_count(pod))

    def collect_api_items(self, fn, **kwargs):
        gateway = self.gateway
        items = []
        complete = True
        for page in gateway.iter_pages(
            fn,
            deadline=_DEADLINE.get() or gateway.clock() + gateway.settings.tool_timeout_seconds,
            **kwargs,
        ):
            items.extend(page.items)
            complete = complete and page.complete
        return (items, complete)

    def metrics_snapshot(self):
        gateway = self.gateway
        return gateway.fetch_metrics(
            deadline=_DEADLINE.get() or gateway.clock() + gateway.settings.tool_timeout_seconds
        )

    def active_pods_on_node(self, core, node_name):
        pods, _ = self.collect_api_items(
            core.list_pod_for_all_namespaces,
            field_selector=f"spec.nodeName={node_name}",
            watch=False,
        )
        return [
            pod for pod in pods if getattr(pod.status, "phase", None) not in {"Succeeded", "Failed"}
        ]

    @_operation
    def list_nodes(
        self,
        label_selector: Annotated[
            str | None, Field(description="Optional Kubernetes label selector")
        ] = None,
        limit: Annotated[int, Field(ge=1, le=200)] = 100,
        continue_token: str | None = None,
    ) -> dict[str, Any]:
        _validate_page_size(limit)
        kube = self.gateway.clients
        page = self.call(
            kube.core.list_node,
            label_selector=label_selector or None,
            limit=limit,
            _continue=continue_token,
        )
        nodes, token = _page_parts(page)
        items = [_parse_node(node) for node in nodes]
        return {
            "cluster": kube.display_name,
            "count": len(items),
            "returned": len(items),
            "items": items,
            "continue_token": token,
            "coverage_scope": "page",
        }

    @_operation
    def get_node_detail(
        self, node_name: Annotated[str, Field(description="节点名称")]
    ) -> dict[str, Any]:
        """获取单个节点的详细信息，包括基础属性、实时指标与节点上 Pod 概览。"""
        kube = self.gateway.clients
        node = self.call(kube.core.read_node, node_name)
        pods = self.active_pods_on_node(kube.core, node_name)
        metrics = self.node_metric_view(kube.custom, node_name)
        detail = _parse_node(node)
        detail["pod_count"] = len(pods)
        detail["pods_sample"] = [
            self.pod_basic_view(pod, self.settings.thresholds) for pod in pods[:20]
        ]
        detail["metrics"] = metrics
        return detail

    @_operation
    def get_node_resource_usage(
        self, node_name: Annotated[str, Field(description="节点名称")]
    ) -> dict[str, Any]:
        """
        审计节点 Pod requests/limits，按 requests 贡献提供取证建议，未知模型显式标记。
        """
        kube = self.gateway.clients
        cfg = self.settings
        node = self.call(kube.core.read_node, node_name)
        pods = self.active_pods_on_node(kube.core, node_name)
        allocatable = node.status.allocatable if node.status else {}
        capacity = node.status.capacity if node.status else {}
        total_cpu_req_m = 0
        total_cpu_lim_m = 0
        total_mem_req_b = 0
        total_mem_lim_b = 0
        pod_breakdown: list[dict[str, Any]] = []
        best_effort_pods: list[dict[str, str]] = []
        resources_complete = True
        unbounded_cpu = unbounded_memory = False
        contributors = []
        for pod in pods:
            pod_res = _pod_effective_resources(pod)
            resources_complete = resources_complete and pod_res.complete
            for warning in pod_res.warnings:
                _mark_incomplete(warning)
            unbounded_cpu = unbounded_cpu or pod_res.cpu_limit_m is None
            unbounded_memory = unbounded_memory or pod_res.memory_limit_bytes is None
            contributors.append(
                {
                    "name": _safe_name(pod),
                    "namespace": _value(pod.metadata, "namespace", "") or "",
                    "cpu_request_m": pod_res.cpu_request_m,
                    "memory_request_bytes": pod_res.memory_request_bytes,
                }
            )
            total_cpu_req_m += pod_res.cpu_request_m
            total_cpu_lim_m += pod_res.configured_cpu_limit_m
            total_mem_req_b += pod_res.memory_request_bytes
            total_mem_lim_b += pod_res.configured_memory_limit_bytes
            qos_class = _pod_qos_class(pod)
            if qos_class == "BestEffort":
                best_effort_pods.append(
                    {
                        "namespace": getattr(pod.metadata, "namespace", "") or "",
                        "name": getattr(pod.metadata, "name", "") or "",
                    }
                )
            if len(pod_breakdown) < 50:
                pod_breakdown.append(
                    {
                        "namespace": getattr(pod.metadata, "namespace", "") or "",
                        "name": getattr(pod.metadata, "name", "") or "",
                        "qos_class": qos_class,
                        "cpu_request_m": pod_res.cpu_request_m,
                        "cpu_limit_m": pod_res.configured_cpu_limit_m,
                        "memory_request_bytes": pod_res.memory_request_bytes,
                        "memory_limit_bytes": pod_res.configured_memory_limit_bytes,
                        "restart_count": _pod_restart_count(pod),
                    }
                )
        alloc_cpu_m = _cpu_to_millicores(str(allocatable.get("cpu", "0")))
        alloc_mem_b = _memory_to_bytes(str(allocatable.get("memory", "0")))
        cap_cpu_m = _cpu_to_millicores(str(capacity.get("cpu", "0")))
        cap_mem_b = _memory_to_bytes(str(capacity.get("memory", "0")))
        cpu_req_ratio = _ratio(total_cpu_req_m, alloc_cpu_m)
        mem_req_ratio = _ratio(total_mem_req_b, alloc_mem_b)
        cpu_lim_ratio = None if unbounded_cpu else _ratio(total_cpu_lim_m, alloc_cpu_m)
        mem_lim_ratio = None if unbounded_memory else _ratio(total_mem_lim_b, alloc_mem_b)
        audit_coverage = _COVERAGE.get()
        audit_complete = audit_coverage is None or audit_coverage["complete"]
        metric_view = self.node_metric_view(kube.custom, node_name)
        eviction_suggestion = None
        complete = resources_complete and audit_complete and (alloc_cpu_m > 0) and (alloc_mem_b > 0)
        if not complete:
            _mark_incomplete("Resource audit incomplete; overcommit is unknown")
        requests_overcommit = (
            cpu_req_ratio > cfg.thresholds.requests_overcommit_ratio
            or mem_req_ratio > cfg.thresholds.requests_overcommit_ratio
            if complete
            else None
        )
        limits_overcommit = (
            cpu_lim_ratio > cfg.thresholds.limits_overcommit_ratio
            or mem_lim_ratio > cfg.thresholds.limits_overcommit_ratio
            if complete and cpu_lim_ratio is not None and (mem_lim_ratio is not None)
            else None
        )
        limits_warning = (
            {
                "reason": "Some container limits are unbounded; configured sums are not total caps",
                "threshold": cfg.thresholds.limits_overcommit_ratio,
            }
            if unbounded_cpu or unbounded_memory
            else None
        )
        if limits_overcommit:
            limits_warning = {
                "reason": "Configured limits exceed allocatable threshold",
                "threshold": cfg.thresholds.limits_overcommit_ratio,
            }
        advice = resource_advice(contributors, {"requests_overcommit": requests_overcommit})
        return {
            "node": node_name,
            "cluster": kube.display_name,
            "pod_count": len(pods),
            "allocatable": {
                "cpu_m": alloc_cpu_m,
                "cpu_human": _format_cpu_m(alloc_cpu_m),
                "memory_bytes": alloc_mem_b,
                "memory_human": _format_bytes(alloc_mem_b),
            },
            "capacity": {
                "cpu_m": cap_cpu_m,
                "cpu_human": _format_cpu_m(cap_cpu_m),
                "memory_bytes": cap_mem_b,
                "memory_human": _format_bytes(cap_mem_b),
            },
            "requests": {
                "cpu_m": total_cpu_req_m,
                "cpu_human": _format_cpu_m(total_cpu_req_m),
                "memory_bytes": total_mem_req_b,
                "memory_human": _format_bytes(total_mem_req_b),
                "cpu_ratio": cpu_req_ratio,
                "memory_ratio": mem_req_ratio,
            },
            "limits": {
                "cpu_m": total_cpu_lim_m,
                "cpu_human": _format_cpu_m(total_cpu_lim_m),
                "memory_bytes": total_mem_lim_b,
                "memory_human": _format_bytes(total_mem_lim_b),
                "cpu_ratio": cpu_lim_ratio,
                "memory_ratio": mem_lim_ratio,
            },
            "live_usage": metric_view,
            "resource_advice": advice,
            "limits_unbounded": {"cpu": unbounded_cpu, "memory": unbounded_memory},
            "overcommit": {
                "requests": requests_overcommit,
                "limits": limits_overcommit,
                "requests_threshold": cfg.thresholds.requests_overcommit_ratio,
                "limits_threshold": cfg.thresholds.limits_overcommit_ratio,
            },
            "eviction_suggestion": eviction_suggestion,
            "limits_warning": limits_warning,
            "pod_breakdown": pod_breakdown,
        }

    @_operation
    def find_problem_nodes(
        self,
        label_selector: Annotated[
            str | None, Field(description="Optional node label filter")
        ] = None,
        max_results: Annotated[int, Field(ge=1, le=200)] = 100,
    ) -> dict[str, Any]:
        _validate_page_size(max_results)
        kube = self.gateway.clients
        cfg = self.settings
        nodes, scan_complete = self.collect_api_items(
            kube.core.list_node, label_selector=label_selector or None
        )
        snapshot = self.scan_snapshot(nodes)
        items = []
        for node in nodes:
            reasons, details = evaluate_node(node, snapshot, self.gateway.settings)
            if reasons:
                items.append(
                    {
                        "name": _safe_name(node),
                        "status": "Ready"
                        if _get_conditions(node).get("Ready") == "True"
                        else "NotReady",
                        "internal_ip": _get_node_addresses(node).get("InternalIP", ""),
                        "age": _format_age(getattr(node.metadata, "creation_timestamp", None)),
                        "reasons": reasons,
                        "conditions": _get_conditions(node),
                        "metrics": details,
                    }
                )
        ranks = {
            "NodeNotReady": 0,
            "DiskPressure": 1,
            "MemoryPressure": 1,
            "PIDPressure": 1,
            "NetworkUnavailable": 1,
            "HighCPUUsage": 2,
            "HighMemoryUsage": 2,
            "Unschedulable": 3,
        }
        items.sort(
            key=lambda item: (min(ranks.get(reason, 4) for reason in item["reasons"]), item["name"])
        )
        truncated = len(items) > max_results
        if truncated:
            _mark_incomplete("Problem-node output truncated; narrow the label selector")
        return {
            "cluster": kube.display_name,
            "count": len(items[:max_results]),
            "items": items[:max_results],
            "thresholds": {
                "node_cpu_utilization": cfg.thresholds.node_cpu_utilization,
                "node_memory_utilization": cfg.thresholds.node_memory_utilization,
            },
            "scanned_count": len(nodes),
            "matched_count": len(items) if scan_complete and snapshot.complete else None,
            "output_truncated": truncated,
            "complete": scan_complete and snapshot.complete and (not truncated),
            "coverage_scope": "scan",
        }

    @_operation
    def list_node_pods(
        self,
        node_name: Annotated[str, Field(description="Node name")],
        limit: Annotated[int, Field(ge=1, le=200)] = 100,
        continue_token: str | None = None,
    ) -> dict[str, Any]:
        _validate_page_size(limit)
        kube = self.gateway.clients
        page = self.call(
            kube.core.list_pod_for_all_namespaces,
            field_selector=f"spec.nodeName={node_name}",
            limit=limit,
            _continue=continue_token,
        )
        pods, token = _page_parts(page)
        items = [
            self.pod_basic_view(pod, self.settings.thresholds)
            for pod in pods
            if getattr(pod.status, "phase", None) not in {"Succeeded", "Failed"}
        ]
        return {
            "cluster": kube.display_name,
            "node": node_name,
            "count": len(items),
            "returned": len(items),
            "items": items,
            "continue_token": token,
            "coverage_scope": "page",
        }

    @_operation
    def diagnose_pod_issues(
        self,
        pod_name: Annotated[str, Field(description="Pod 名称")],
        namespace: Annotated[str, Field(description="Pod 所在命名空间")],
    ) -> dict[str, Any]:
        """
        对指定 Pod 做精简版诊断：
        - Pending：补充事件信息
        - CrashLoopBackOff / ImagePullBackOff 等：尝试读取 previous 日志
        """
        kube = self.gateway.clients
        pod = self.call(kube.core.read_namespaced_pod, name=pod_name, namespace=namespace)
        phase = pod.status.phase if pod.status else ""
        findings = self.pod_findings(pod)
        issue = summarize_issue(findings)
        problem_container = select_problem_container(pod, findings)
        gateway = self.gateway
        deadline = _DEADLINE.get()
        event_result = read_events(
            gateway,
            namespace=namespace,
            pod_uid=getattr(pod.metadata, "uid", None),
            deadline=deadline,
        )
        log_result = EvidenceResult("")
        if (
            problem_container
            and issue
            and any(
                reason in issue
                for reason in ("CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull", "OOMKilled")
            )
        ):
            log_result = read_log_excerpt(
                gateway,
                namespace=namespace,
                pod_name=pod_name,
                container_name=problem_container,
                deadline=deadline,
            )
        for warning in event_result.warnings + log_result.warnings:
            _mark_incomplete(warning)
        events = event_result.data
        log_excerpt = log_result.data
        return {
            "cluster": kube.display_name,
            "namespace": namespace,
            "name": pod_name,
            "node": getattr(pod.spec, "node_name", "") or "",
            "phase": phase,
            "qos_class": _pod_qos_class(pod),
            "restart_count": _pod_restart_count(pod),
            "issue": issue,
            "problem_container": problem_container,
            "events": events,
            "log_excerpt": log_excerpt,
            "findings": [asdict(f) for f in findings],
            "suggestions": _pod_suggestions(issue, phase, findings),
        }

    @_operation
    def health_check(self) -> dict[str, Any]:
        kube = self.gateway.clients
        self.call(kube.core.list_node, limit=1)
        return {
            "status": "healthy",
            "cluster": kube.display_name,
            "node_count": None,
            "warnings": ["Connectivity check does not count all nodes"],
        }

    @_operation
    def cluster_summary(self) -> dict[str, Any]:
        kube = self.gateway.clients
        nodes, complete = self.collect_api_items(kube.core.list_node)
        snapshot = self.scan_snapshot(nodes)
        problems = sum(
            bool(evaluate_node(node, snapshot, self.gateway.settings)[0]) for node in nodes
        )
        return {
            "cluster": kube.display_name,
            "node_count": len(nodes) if complete else None,
            "scanned_count": len(nodes),
            "problem_node_count": problems if complete and snapshot.complete else None,
            "observed_problem_node_count": problems,
            "complete": complete and snapshot.complete,
            "coverage_scope": "scan",
            "available_tools": [
                "list_nodes",
                "get_node_detail",
                "get_node_resource_usage",
                "find_problem_nodes",
                "list_node_pods",
                "diagnose_pod_issues",
                "health_check",
            ],
        }
