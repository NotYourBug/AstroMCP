from __future__ import annotations

import re
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from kubernetes import client

from .models import _value
from .quantities import _cpu_to_millicores, _memory_to_bytes


def _now_utc() -> datetime:
    """返回 UTC 当前时间（用于计算资源年龄等）。"""
    return datetime.now(timezone.utc)


def _format_age(creation_timestamp: datetime | None) -> str:
    """把创建时间转换成紧凑的 age（m/h/d），用于节点列表展示。"""
    if creation_timestamp is None:
        return ""
    delta = _now_utc() - creation_timestamp.astimezone(timezone.utc)
    total_seconds = int(delta.total_seconds())
    if total_seconds < 0:
        return "0m"
    if total_seconds < 3600:
        return f"{max(total_seconds // 60, 1)}m"
    if total_seconds < 86400:
        return f"{total_seconds // 3600}h"
    return f"{total_seconds // 86400}d"


def _safe_name(obj: Any) -> str:
    """安全获取资源对象名称（metadata.name），避免空对象导致异常。"""
    metadata = getattr(obj, "metadata", None)
    return getattr(metadata, "name", "") or ""


def _format_cpu_m(cpu_m: int) -> str:
    """把 millicores 转成可读字符串。"""
    return f"{cpu_m}m"


def _format_bytes(num_bytes: int) -> str:
    """把 bytes 转成 Ki/Mi/Gi 形式的可读字符串（用于输出展示）。"""
    if num_bytes < 1024:
        return f"{num_bytes}B"
    units = ["Ki", "Mi", "Gi", "Ti", "Pi"]
    value = float(num_bytes)
    for unit in units:
        value /= 1024.0
        if value < 1024.0:
            return f"{value:.1f}{unit}"
    return f"{value:.1f}Ei"


def _ratio(numerator, denominator):
    return round(numerator / denominator, 4) if denominator > 0 else None


def _get_node_addresses(node: client.V1Node) -> dict[str, str]:
    """提取 Node 地址（InternalIP/Hostname 等）为字典，便于展示与过滤。"""
    addresses: dict[str, str] = {}
    for addr in getattr(node.status, "addresses", None) or []:
        addr_type = addr.type or "Unknown"
        addr_value = addr.address or ""
        if addr_value:
            addresses[addr_type] = addr_value
    return addresses


def _get_conditions(node: client.V1Node) -> dict[str, str]:
    """提取 Node Conditions（Ready/DiskPressure/...）为扁平化字典。"""
    conditions: dict[str, str] = {}
    for cond in getattr(node.status, "conditions", None) or []:
        conditions[cond.type or "Unknown"] = cond.status or "Unknown"
    return conditions


def _taints_to_list(node: client.V1Node) -> list[str]:
    """把 taints 转成更直观的一维列表字符串，便于 LLM 阅读。"""
    out: list[str] = []
    for taint in getattr(node.spec, "taints", None) or []:
        key = taint.key or ""
        effect = taint.effect or ""
        value = taint.value or ""
        if value:
            out.append(f"{key}={value}:{effect}")
        else:
            out.append(f"{key}:{effect}")
    return out


def _parse_node(node: client.V1Node) -> dict[str, Any]:
    """把复杂 Node 对象压平为更适合 LLM 阅读的结构。"""
    conditions = _get_conditions(node)
    addresses = _get_node_addresses(node)
    info = getattr(node.status, "node_info", None)
    allocatable = getattr(node.status, "allocatable", None) or {}
    capacity = getattr(node.status, "capacity", None) or {}
    return {
        "name": _safe_name(node),
        "status": "Ready" if conditions.get("Ready") == "True" else "NotReady",
        "age": _format_age(getattr(node.metadata, "creation_timestamp", None)),
        "internal_ip": addresses.get("InternalIP", ""),
        "hostname": addresses.get("Hostname", ""),
        "unschedulable": bool(getattr(node.spec, "unschedulable", False)),
        "taints": _taints_to_list(node),
        "conditions": conditions,
        "os_image": getattr(info, "os_image", "") or "",
        "kernel_version": getattr(info, "kernel_version", "") or "",
        "kubelet_version": getattr(info, "kubelet_version", "") or "",
        "container_runtime": getattr(info, "container_runtime_version", "") or "",
        "capacity": {
            "cpu": str(capacity.get("cpu", "")),
            "memory": str(capacity.get("memory", "")),
            "pods": str(capacity.get("pods", "")),
        },
        "allocatable": {
            "cpu": str(allocatable.get("cpu", "")),
            "memory": str(allocatable.get("memory", "")),
            "pods": str(allocatable.get("pods", "")),
        },
    }


def _pod_qos_class(pod: client.V1Pod) -> str:
    """读取 Pod QoS Class（Guaranteed/Burstable/BestEffort），用于资源/驱逐建议。"""
    if pod.status and pod.status.qos_class:
        return pod.status.qos_class
    return "Unknown"


def _event_brief(event: client.V1Event) -> dict[str, str]:
    """把 Event 压缩成短结构（原因/类型/消息/时间戳），用于 Pod 诊断输出。"""
    return {
        "reason": event.reason or "",
        "type": event.type or "",
        "message": redact_common_secrets(event.message or "")[:300],
        "last_timestamp": str(
            getattr(event, "last_timestamp", "") or getattr(event, "event_time", "") or ""
        ),
    }


def redact_common_secrets(text: str) -> str:
    text = re.sub(
        "-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)",
        "[REDACTED PRIVATE KEY]",
        text,
        flags=re.DOTALL,
    )
    text = re.sub(
        "(?i)(authorization\\s*:\\s*bearer\\s+)\\S+", lambda match: match[1] + "[REDACTED]", text
    )
    return re.sub(
        "(?i)(\\b(?:password|passwd|token|api[_-]?key|secret)\\b[\"']?\\s*[:=]\\s*)(?:\"[^\"]*\"|'[^']*'|[^\\s,;]+)",
        lambda match: match[1] + "[REDACTED]",
        text,
    )


def node_metric_view(node_name, snapshot):
    usage = snapshot.usage_by_node.get(node_name)
    if not snapshot.available or not usage:
        return None
    try:
        if "cpu" not in usage or "memory" not in usage:
            raise ValueError("missing quantity")
        cpu = _cpu_to_millicores(str(usage["cpu"]))
        memory = _memory_to_bytes(str(usage["memory"]))
    except ValueError:
        return None
    return {
        "cpu_usage_m": cpu,
        "cpu_usage_human": _format_cpu_m(cpu),
        "memory_usage_bytes": memory,
        "memory_usage_human": _format_bytes(memory),
        "observed_at": snapshot.observed_at.isoformat(),
    }


def pod_basic_view(pod, findings, issue, restart_count):
    return {
        "namespace": _value(_value(pod, "metadata"), "namespace", "") or "",
        "name": _value(_value(pod, "metadata"), "name", "") or "",
        "phase": _value(_value(pod, "status"), "phase", "") or "",
        "qos_class": _pod_qos_class(pod),
        "restart_count": restart_count,
        "findings": [asdict(f) for f in findings],
        "issue": issue,
    }
