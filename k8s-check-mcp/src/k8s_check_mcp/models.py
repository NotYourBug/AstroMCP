from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from contextvars import ContextVar
from typing import Any
from kubernetes import client
from kubernetes.client import ApiClient

@dataclass
class MCPKubeClients:
    """MCP Server 用到的 K8s API 客户端集合。"""
    display_name: str
    api_client: ApiClient
    core: client.CoreV1Api
    custom: client.CustomObjectsApi

@dataclass
class PageResult:
    items: list[Any]
    continue_token: str | None = None
    complete: bool = True
    warnings: list[str] = field(default_factory=list)

@dataclass
class MetricsSnapshot:
    usage_by_node: dict
    observed_at: datetime
    available: bool = True
    error_code: str | None = None
    complete: bool = True
    warnings: list[str] = field(default_factory=list)

@dataclass
class Finding:
    code: str
    severity: str
    container: str | None = None
    evidence: dict = field(default_factory=dict)

@dataclass
class PodResources:
    cpu_request_m: int = 0
    memory_request_bytes: int = 0
    cpu_limit_m: int | None = None
    memory_limit_bytes: int | None = None
    complete: bool = True
    warnings: list[str] = field(default_factory=list)
    configured_cpu_limit_m: int = 0
    configured_memory_limit_bytes: int = 0

@dataclass
class EvidenceResult:
    data: Any
    complete: bool = True
    warnings: list[str] = field(default_factory=list)

class K8sOperationError(Exception):

    def __init__(self, code, message):
        self.code = code
        super().__init__(message)

def _value(obj, name, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        camel = name.split('_')[0] + ''.join((part.title() for part in name.split('_')[1:]))
        return obj.get(name, obj.get(camel, default))
    return getattr(obj, name, default)

def _container_statuses(pod):
    status = _value(pod, 'status')
    return [(item, 'app') for item in _value(status, 'container_statuses') or []] + [(item, 'init') for item in _value(status, 'init_container_statuses') or []]

_DEADLINE=ContextVar("k8s_deadline",default=None)
_COVERAGE=ContextVar("k8s_coverage",default=None)
def _mark_incomplete(warning):
    coverage = _COVERAGE.get()
    if coverage is not None:
        coverage['complete'] = False
        if warning not in coverage['warnings'] and len(coverage['warnings']) < 20:
            coverage['warnings'].append(warning)
