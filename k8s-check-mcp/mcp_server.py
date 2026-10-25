"""
基于 FastMCP 的精简版 K8s 巡检 MCP Server。

设计目标：
- 不是把整个项目都改造成 MCP，而是只保留“check/排障”核心逻辑
- 重点暴露给 LLM 的是：节点状态、节点详情、节点资源审计、异常节点识别
- 额外提供：节点上的 Pod 查询、Pod 异常诊断、集群摘要 Resource、排障 Prompt

这份 MCP Server 当前是独立自包含的实现：
- parse_quantity：在本文件内统一处理 K8s 资源单位（m / Mi / Gi ...）
- _pod_issue / _BAD_POD_REASONS：在本文件内完成 Pod 异常判定
- InspectorConfig：在本文件内维护阈值配置，便于单独移植

运行方式：
- 推荐单独使用 Python 3.10+ 虚拟环境安装 fastmcp
- 启动后由 Claude / Trae / 其他 MCP Client 以 stdio 或 HTTP 方式接入
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

logger = logging.getLogger("k8s-check-mcp")

from fastmcp import FastMCP
from kubernetes import client, config
from kubernetes.client import ApiClient
from kubernetes.client.exceptions import ApiException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from collections.abc import Mapping
from typing import Literal
from contextvars import ContextVar
from functools import wraps
from uuid import uuid4
import argparse, ipaddress
from urllib3.exceptions import HTTPError, ReadTimeoutError

# MCP Server 主对象：用于注册 tools/resources/prompts，并对外运行协议服务。
mcp = FastMCP("K8s Check MCP")


@dataclass
class MCPPodResource:
    """单个 Pod 的有效资源画像（调度视角）。"""

    cpu_request_m: int = 0
    cpu_limit_m: int = 0
    memory_request_bytes: int = 0
    memory_limit_bytes: int = 0


@dataclass
class MCPKubeClients:
    """MCP Server 用到的 K8s API 客户端集合。"""

    display_name: str
    api_client: ApiClient
    core: client.CoreV1Api
    custom: client.CustomObjectsApi


class Thresholds(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    node_cpu_utilization: float = Field(default=.80, ge=0, le=1)
    node_memory_utilization: float = Field(default=.85, ge=0, le=1)
    pod_restart_count: int = Field(default=3, ge=1)
    requests_overcommit_ratio: float = Field(default=1, gt=0)
    limits_overcommit_ratio: float = Field(default=1.5, gt=0)
    pending_seconds: float = Field(default=300, gt=0)
    ready_seconds: float = Field(default=60, gt=0)


@dataclass
class InspectorConfig:
    thresholds: Thresholds = field(default_factory=Thresholds)


class K8sSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    thresholds: Thresholds = Field(default_factory=Thresholds)
    in_cluster: bool = False
    kubeconfig: str | None = None
    context: str | None = None
    verify_ssl: bool = True
    connect_timeout_seconds: float = Field(default=3, gt=0)
    read_timeout_seconds: float = Field(default=10, gt=0)
    tool_timeout_seconds: float = Field(default=30, gt=0)
    queue_timeout_seconds: float = Field(default=2, ge=0)
    api_rps: float = Field(default=5, gt=0)
    api_burst: int = Field(default=10, ge=1)
    api_max_in_flight: int = Field(default=3, ge=1)
    rate_limit_enabled: bool = True
    page_size: int = Field(default=200, ge=1, le=200)
    max_objects: int = Field(default=10000, ge=1)
    include_logs: bool = True
    transport: Literal["stdio","http","sse"] = "stdio"
    bind_host: str = "127.0.0.1"
    bind_port: int = Field(default=8080, ge=1, le=65535)

    @model_validator(mode="after")
    def validate_budget(self):
        if self.queue_timeout_seconds >= self.tool_timeout_seconds:
            raise ValueError("Queue budget must be less than tool budget")
        return self


def load_settings(environ: Mapping[str,str], config_path: Path | None = None) -> K8sSettings:
    path = config_path or (Path(environ["MCP_CONFIG"]) if environ.get("MCP_CONFIG") else None)
    data = {}
    if path:
        raw = path.read_text(encoding="utf-8")
        if path.suffix.lower() in {".yaml",".yml"}:
            try:
                import yaml
            except ImportError as exc:
                raise ValueError("YAML requires astromcp-k8s[yaml]") from exc
            try:
                data = yaml.safe_load(raw)
            except yaml.YAMLError as exc:
                raise ValueError("Invalid YAML configuration") from exc
        else:
            data = json.loads(raw)
    if not isinstance(data,dict):
        raise ValueError("Configuration must be an object")
    threshold_data = dict(data.get("thresholds", {}))
    for name in Thresholds.model_fields:
        key = "MCP_THRESHOLD_"+name.upper()
        if key in environ:
            threshold_data[name] = environ[key]
    data["thresholds"] = threshold_data
    names = {"MCP_K8S_IN_CLUSTER":"in_cluster", "MCP_K8S_KUBECONFIG":"kubeconfig", "MCP_K8S_CONTEXT":"context",
             "MCP_K8S_VERIFY_SSL":"verify_ssl", "MCP_K8S_CONNECT_TIMEOUT_SECONDS":"connect_timeout_seconds",
             "MCP_K8S_READ_TIMEOUT_SECONDS":"read_timeout_seconds", "MCP_TOOL_TIMEOUT_SECONDS":"tool_timeout_seconds",
             "MCP_QUEUE_TIMEOUT_SECONDS":"queue_timeout_seconds", "MCP_K8S_API_RPS":"api_rps",
             "MCP_K8S_API_BURST":"api_burst", "MCP_K8S_API_MAX_IN_FLIGHT":"api_max_in_flight",
             "MCP_K8S_RATE_LIMIT_ENABLED":"rate_limit_enabled", "MCP_K8S_PAGE_SIZE":"page_size",
             "MCP_K8S_MAX_OBJECTS":"max_objects", "MCP_K8S_INCLUDE_LOGS":"include_logs",
             "MCP_TRANSPORT":"transport", "MCP_BIND_HOST":"bind_host", "MCP_BIND_PORT":"bind_port"}
    data.update({field:environ[key] for key,field in names.items() if key in environ})
    return K8sSettings.model_validate(data)


class K8sOperationError(Exception):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


class TokenBucketLimiter:
    def __init__(self, rps, burst, max_in_flight, *, queue_timeout=2, enabled=True, clock=time.monotonic):
        self._rps, self._capacity = rps, burst
        self._tokens = float(burst)
        self._clock = clock
        self._updated_at = clock()
        self._lock = threading.Lock()
        self._sem = threading.BoundedSemaphore(max_in_flight)
        self.queue_timeout, self.enabled = queue_timeout, enabled

    def acquire(self, *, deadline):
        now = self._clock()
        if now >= deadline:
            raise K8sOperationError("TIMEOUT", "Operation deadline exceeded")
        end = min(deadline, now+self.queue_timeout)
        if not self._sem.acquire(timeout=max(0,end-now)):
            raise K8sOperationError("BUSY", "API concurrency budget exhausted")
        try:
            while self.enabled:
                with self._lock:
                    now = self._clock()
                    self._tokens = min(self._capacity,self._tokens+max(now-self._updated_at,0)*self._rps)
                    self._updated_at = now
                    if self._tokens >= 1:
                        self._tokens -= 1
                        return
                    wait = (1-self._tokens)/self._rps
                if now >= end:
                    raise K8sOperationError("BUSY", "API rate-limit queue budget exhausted")
                time.sleep(min(wait,max(0,end-now),.05))
        except BaseException:
            self._sem.release()
            raise

    def release(self):
        self._sem.release()


def init_k8s_client(settings=None) -> MCPKubeClients:
    settings = settings or load_settings(os.environ)
    cfg = client.Configuration()
    if settings.in_cluster:
        config.load_incluster_config(client_configuration=cfg)
    else:
        config.load_kube_config(config_file=settings.kubeconfig, context=settings.context, client_configuration=cfg)
    cfg.verify_ssl = settings.verify_ssl
    cfg.retries = 0
    cfg.connection_pool_maxsize = settings.api_max_in_flight
    api_client = client.ApiClient(cfg)
    return MCPKubeClients("in-cluster" if settings.in_cluster else settings.context or "kubeconfig",
                          api_client,client.CoreV1Api(api_client),client.CustomObjectsApi(api_client))


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


def _page_parts(page):
    if isinstance(page,dict):
        return page.get("items",[]), page.get("metadata",{}).get("continue")
    return page.items, getattr(getattr(page,"metadata",None),"_continue",None)


class K8sGateway:
    def __init__(self, settings: K8sSettings, *, client_factory=None, clock=time.monotonic):
        self.settings, self.clock = settings, clock
        self._factory = client_factory or (lambda:init_k8s_client(settings))
        self._clients = None
        self._lock = threading.Lock()
        self._closed = False
        self.limiter = TokenBucketLimiter(settings.api_rps,settings.api_burst,settings.api_max_in_flight,
                         queue_timeout=settings.queue_timeout_seconds,enabled=settings.rate_limit_enabled,clock=clock)

    @property
    def clients(self):
        with self._lock:
            if self._closed:
                raise K8sOperationError("BUSY", "K8s gateway is closed")
            if self._clients is None:
                self._clients = self._factory()
            return self._clients

    def call(self, fn, *args, deadline, **kwargs):
        self.limiter.acquire(deadline=deadline)
        try:
            remaining = deadline-self.clock()
            if remaining <= 0:
                raise K8sOperationError("TIMEOUT", "Operation deadline exceeded")
            kwargs["_request_timeout"] = (min(self.settings.connect_timeout_seconds,remaining),
                                          min(self.settings.read_timeout_seconds,remaining))
            return fn(*args,**kwargs)
        except ApiException as exc:
            code = {401:"AUTH_FAILED",403:"PERMISSION_DENIED",404:"NOT_FOUND",429:"BUSY"}.get(exc.status,"COMMAND_FAILED")
            raise K8sOperationError(code,"Kubernetes API request failed") from exc
        except (TimeoutError, ReadTimeoutError):
            raise K8sOperationError("TIMEOUT", "Kubernetes API timed out") from None
        except (OSError, HTTPError):
            raise K8sOperationError("UNREACHABLE", "Kubernetes API unavailable") from None
        finally:
            self.limiter.release()

    def iter_pages(self, fn, *, deadline, **kwargs):
        token, seen, count = None, set(), 0
        while True:
            try:
                page = self.call(fn,deadline=deadline,limit=self.settings.page_size,_continue=token,**kwargs)
            except K8sOperationError as exc:
                _mark_incomplete(exc.code)
                yield PageResult([],None,False,[exc.code])
                return
            items, next_token = _page_parts(page)
            if next_token and next_token in seen:
                _mark_incomplete("Repeated API continuation token")
                yield PageResult([],None,False,["Repeated API continuation token"])
                return
            remaining = self.settings.max_objects-count
            if len(items)>remaining or (len(items)==remaining and next_token):
                _mark_incomplete("API object limit reached")
                yield PageResult(list(items[:remaining]),None,False,["API object limit reached"])
                return
            yield PageResult(list(items),next_token)
            count += len(items)
            if not next_token:
                return
            seen.add(next_token)
            token = next_token

    def fetch_metrics(self, *, deadline):
        observed = datetime.now(timezone.utc)
        try:
            obj = self.call(self.clients.custom.list_cluster_custom_object,deadline=deadline,
                            group="metrics.k8s.io",version="v1beta1",plural="nodes")
        except K8sOperationError as exc:
            _mark_incomplete("Node metrics unavailable: "+exc.code)
            return MetricsSnapshot({},observed,False,exc.code,False,[exc.code])
        if not isinstance(obj,dict) or not isinstance(obj.get("items"),list):
            _mark_incomplete("Node metrics malformed")
            return MetricsSnapshot({},observed,False,"PARSE_ERROR",False,["PARSE_ERROR"])
        usage={}
        complete=len(obj["items"])<=self.settings.max_objects
        for item in obj["items"][:self.settings.max_objects]:
            if not isinstance(item,dict) or not isinstance(item.get("metadata"),dict) or not isinstance(item.get("usage"),dict):
                complete=False
                continue
            name=item["metadata"].get("name")
            if isinstance(name,str):usage[name]=item["usage"]
        if not complete:_mark_incomplete("Node metrics incomplete or object limit reached")
        return MetricsSnapshot(usage,observed,True,None,complete,[] if complete else ["Metrics incomplete"])

    def close(self):
        with self._lock:
            self._closed = True
            if self._clients is not None:
                self._clients.api_client.close()


_GATEWAY = None
_GATEWAY_CONTEXT = ContextVar("k8s_gateway", default=None)
_DEADLINE = ContextVar("k8s_deadline", default=None)
_COVERAGE = ContextVar("k8s_coverage", default=None)


def _get_gateway():
    global _GATEWAY
    if _GATEWAY_CONTEXT.get() is not None:
        return _GATEWAY_CONTEXT.get()
    if _GATEWAY is None:
        _GATEWAY = K8sGateway(load_settings(os.environ))
    return _GATEWAY


def _get_k8s_clients():
    return _get_gateway().clients


def _get_runtime_config():
    return InspectorConfig(_get_gateway().settings.thresholds)


def _k8s_api_call(fn, *args, **kwargs):
    gateway = _get_gateway()
    return gateway.call(fn,*args,deadline=_DEADLINE.get() or gateway.clock()+gateway.settings.tool_timeout_seconds,**kwargs)


def _mark_incomplete(warning):
    coverage = _COVERAGE.get()
    if coverage is not None:
        coverage["complete"] = False
        if warning not in coverage["warnings"] and len(coverage["warnings"])<20:
            coverage["warnings"].append(warning)


def _k8s_tool(fn):
    @wraps(fn)
    def wrapped(*args,**kwargs):
        gateway = _get_gateway()
        token = _DEADLINE.set(gateway.clock()+gateway.settings.tool_timeout_seconds)
        coverage = {"complete":True,"warnings":[]}
        state = _COVERAGE.set(coverage)
        request_id = str(uuid4())
        try:
            try:
                result = fn(*args,**kwargs)
            except K8sOperationError as exc:
                result = {"ok":False,"error":str(exc),"error_code":exc.code,"complete":False}
            except config.ConfigException:
                result = {"ok":False,"error":"Kubernetes configuration is unavailable","error_code":"CONFIG_ERROR","complete":False}
            result.setdefault("ok","error" not in result)
            if fn.__name__ == "health_check" and not result["ok"]:
                result["status"] = "unhealthy"
            result["complete"] = result.get("complete",True) and coverage["complete"]
            result["warnings"] = list(dict.fromkeys(result.get("warnings",[])+coverage["warnings"]))
            result.update(request_id=request_id,timestamp=datetime.now(timezone.utc).isoformat())
            logger.info("tool=%s request_id=%s complete=%s",fn.__name__,request_id,result["complete"])
            return result
        finally:
            _DEADLINE.reset(token)
            _COVERAGE.reset(state)
    return wrapped


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
# getattr是用来动态获取、设置对象的属性，常用于反射、动态编程。
# getattr(object, name, default=None)

_DECIMAL = {
    "n": 1e-9,
    "u": 1e-6,
    "m": 1e-3,
    "": 1.0,
    "k": 1e3,
    "M": 1e6,
    "G": 1e9,
    "T": 1e12,
    "P": 1e15,
    "E": 1e18,
}

_BINARY = {
    "Ki": 1024.0,
    "Mi": 1024.0**2,
    "Gi": 1024.0**3,
    "Ti": 1024.0**4,
    "Pi": 1024.0**5,
    "Ei": 1024.0**6,
}


def parse_quantity(q: str) -> float:
    """
    解析 K8s resource quantity 字符串为数值。

    返回单位：
    - cpu：以“核”为基准（m 表示 1e-3）
    - memory：以“字节”为基准（Ki/Mi/Gi 等为 1024 进位）
    """

    s = (q or "").strip()
    if not s:
        return 0.0
    for suf, mult in _BINARY.items():
        if s.endswith(suf):
            num_part = s[: -len(suf)]
            return float(num_part) * mult if num_part else 0.0
    for suf, mult in _DECIMAL.items():
        if suf and s.endswith(suf):
            num_part = s[: -len(suf)]
            return float(num_part) * mult if num_part else 0.0
    return float(s) if s else 0.0



def _cpu_to_millicores(quantity: str) -> int:
    """把 K8s CPU quantity（如 250m/1）统一换算成 millicores（m）。"""
    return int(round(parse_quantity(quantity) * 1000))


def _memory_to_bytes(quantity: str) -> int:
    """把 K8s 内存 quantity（如 128Mi/1Gi）统一换算成 bytes。"""
    return int(round(parse_quantity(quantity)))


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


def _ratio(numerator: int, denominator: int) -> float:
    """计算占比，避免除 0，并做 4 位小数截断。"""
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def _get_node_addresses(node: client.V1Node) -> dict[str, str]:
    """提取 Node 地址（InternalIP/Hostname 等）为字典，便于展示与过滤。"""
    addresses: dict[str, str] = {}
    for addr in node.status.addresses or []:
        addr_type = addr.type or "Unknown"
        addr_value = addr.address or ""
        if addr_value:
            addresses[addr_type] = addr_value
    return addresses


def _get_conditions(node: client.V1Node) -> dict[str, str]:
    """提取 Node Conditions（Ready/DiskPressure/...）为扁平化字典。"""
    conditions: dict[str, str] = {}
    for cond in node.status.conditions or []:
        conditions[cond.type or "Unknown"] = cond.status or "Unknown"
    return conditions


def _taints_to_list(node: client.V1Node) -> list[str]:
    """把 taints 转成更直观的一维列表字符串，便于 LLM 阅读。"""
    out: list[str] = []
    for taint in node.spec.taints or []:
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
    info = node.status.node_info if node.status else None
    allocatable = node.status.allocatable if node.status else {}
    capacity = node.status.capacity if node.status else {}
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


def _active_pods_on_node(core, node_name):
    pods, _ = _collect_api_items(core.list_pod_for_all_namespaces,field_selector=f"spec.nodeName={node_name}",watch=False)
    return [pod for pod in pods if getattr(pod.status,"phase",None) not in {"Succeeded","Failed"}]


def _sum_container_resources(containers: list[Any]) -> tuple[int, int, int, int]:
    """对一组容器的 requests/limits 进行求和（CPU m / Memory bytes）。"""
    cpu_request_m = 0
    cpu_limit_m = 0
    mem_request_b = 0
    mem_limit_b = 0
    for container in containers:
        resources = getattr(container, "resources", None)
        requests = getattr(resources, "requests", None) or {}
        limits = getattr(resources, "limits", None) or {}
        cpu_request_m += _cpu_to_millicores(str(requests.get("cpu", "0")))
        cpu_limit_m += _cpu_to_millicores(str(limits.get("cpu", "0")))
        mem_request_b += _memory_to_bytes(str(requests.get("memory", "0")))
        mem_limit_b += _memory_to_bytes(str(limits.get("memory", "0")))
    return cpu_request_m, cpu_limit_m, mem_request_b, mem_limit_b


def _max_container_resources(containers: list[Any]) -> tuple[int, int, int, int]:
    """对一组容器取各资源维度的最大值（用于 init containers，因为它们是串行执行的）。"""
    max_cpu_req = 0
    max_cpu_lim = 0
    max_mem_req = 0
    max_mem_lim = 0
    for container in containers:
        resources = getattr(container, "resources", None)
        requests = getattr(resources, "requests", None) or {}
        limits = getattr(resources, "limits", None) or {}
        max_cpu_req = max(max_cpu_req, _cpu_to_millicores(str(requests.get("cpu", "0"))))
        max_cpu_lim = max(max_cpu_lim, _cpu_to_millicores(str(limits.get("cpu", "0"))))
        max_mem_req = max(max_mem_req, _memory_to_bytes(str(requests.get("memory", "0"))))
        max_mem_lim = max(max_mem_lim, _memory_to_bytes(str(limits.get("memory", "0"))))
    return max_cpu_req, max_cpu_lim, max_mem_req, max_mem_lim


def _pod_effective_resources(pod: client.V1Pod) -> MCPPodResource:
    """
    计算单个 Pod 的有效 Requests/Limits。

    调度视角下，InitContainer 采用 max(init) 与 sum(containers) 的组合逻辑。
    """

    containers = pod.spec.containers or []
    init_containers = pod.spec.init_containers or []

    c_req_cpu, c_lim_cpu, c_req_mem, c_lim_mem = _sum_container_resources(containers)
    i_req_cpu, i_lim_cpu, i_req_mem, i_lim_mem = _max_container_resources(init_containers)

    # K8s 调度口径：
    # - 普通容器资源是 sum(containers)
    # - initContainers 按串行执行，取每个 init container 在各资源维度上的最大值
    # 有效资源 = max(sum(containers), max_per_init_container)
    return MCPPodResource(
        cpu_request_m=max(c_req_cpu, i_req_cpu),
        cpu_limit_m=max(c_lim_cpu, i_lim_cpu),
        memory_request_bytes=max(c_req_mem, i_req_mem),
        memory_limit_bytes=max(c_lim_mem, i_lim_mem),
    )


def _pod_restart_count(pod):
    return sum(int(_value(cs,"restart_count",0) or 0) for cs,_ in _container_statuses(pod))


def _pod_qos_class(pod: client.V1Pod) -> str:
    """读取 Pod QoS Class（Guaranteed/Burstable/BestEffort），用于资源/驱逐建议。"""
    if pod.status and pod.status.qos_class:
        return pod.status.qos_class
    return "Unknown"


def _pod_basic_view(pod, thresholds=None):
    findings=evaluate_pod(pod,thresholds=thresholds or Thresholds(),now=_now_utc())
    return {"namespace":_value(_value(pod,"metadata"),"namespace","") or "",
            "name":_value(_value(pod,"metadata"),"name","") or "",
            "phase":_value(_value(pod,"status"),"phase","") or "", "qos_class":_pod_qos_class(pod),
            "restart_count":_pod_restart_count(pod),"findings":[asdict(f) for f in findings],"issue":summarize_issue(findings)}


def _node_metric_view(custom, node_name, snapshot=None):
    snapshot = snapshot if snapshot is not None else _metrics_snapshot()
    usage = snapshot.usage_by_node.get(node_name)
    if not snapshot.available or not usage:
        _mark_incomplete("Node usage not checked: metrics missing")
        return None
    try:
        if "cpu" not in usage or "memory" not in usage:
            raise ValueError("missing quantity")
        cpu = _cpu_to_millicores(str(usage["cpu"]))
        memory = _memory_to_bytes(str(usage["memory"]))
    except ValueError:
        _mark_incomplete("Node metrics quantity invalid")
        return None
    return {"cpu_usage_m":cpu,"cpu_usage_human":_format_cpu_m(cpu),
            "memory_usage_bytes":memory,"memory_usage_human":_format_bytes(memory),
            "observed_at":snapshot.observed_at.isoformat()}

def get_node_metrics(custom):
    snapshot = _metrics_snapshot()
    return snapshot.usage_by_node if snapshot.available else None


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


@dataclass
class Finding:
    code: str
    severity: str
    container: str | None = None
    evidence: dict = field(default_factory=dict)


def _value(obj, name, default=None):
    if obj is None:
        return default
    if isinstance(obj,dict):
        camel = name.split("_")[0]+"".join(part.title() for part in name.split("_")[1:])
        return obj.get(name,obj.get(camel,default))
    return getattr(obj,name,default)


def _age_seconds(timestamp, now):
    if isinstance(timestamp,str):
        try:
            timestamp = datetime.fromisoformat(timestamp.replace("Z","+00:00"))
        except ValueError:
            return None
    if not isinstance(timestamp,datetime) or timestamp.tzinfo is None:
        return None
    age=(now-timestamp).total_seconds()
    return age if age >= 0 else None


def _container_statuses(pod):
    status=_value(pod,"status")
    return [(item,"app") for item in (_value(status,"container_statuses") or [])]+[(item,"init") for item in (_value(status,"init_container_statuses") or [])]


def evaluate_pod(pod, *, thresholds: Thresholds, now: datetime) -> list[Finding]:
    status=_value(pod,"status")
    if status is None:
        return [Finding("MissingStatus","warning")]
    phase=_value(status,"phase","")
    findings=[]
    if phase in {"Failed","Unknown"}:
        findings.append(Finding("Pod"+phase,"error",evidence={"phase":phase}))
    for cs,kind in _container_statuses(pod):
        name=_value(cs,"name")
        state=_value(cs,"state")
        waiting=_value(state,"waiting")
        terminated=_value(state,"terminated")
        previous=_value(_value(cs,"last_state"),"terminated")
        reason=_value(waiting,"reason")
        if reason in _BAD_POD_REASONS:
            findings.append(Finding(reason,"error",name,{"state":"waiting","kind":kind}))
        if terminated is not None and (_value(terminated,"exit_code",0) or _value(terminated,"reason") in _BAD_POD_REASONS):
            findings.append(Finding(_value(terminated,"reason") or "ContainerTerminated","error",name,
                                    {"state":"terminated","kind":kind,"exit_code":_value(terminated,"exit_code")}))
        if previous is not None and _value(previous,"reason") in _BAD_POD_REASONS:
            findings.append(Finding(_value(previous,"reason"),"info",name,{"state":"historical","kind":kind}))
        restarts=_value(cs,"restart_count",0) or 0
        if isinstance(restarts,int) and restarts>=thresholds.pod_restart_count:
            findings.append(Finding("RestartCount","warning",name,{"count":restarts,"threshold":thresholds.pod_restart_count,"kind":kind}))
    created=_value(_value(pod,"metadata"),"creation_timestamp")
    if phase=="Pending":
        age=_age_seconds(created,now)
        severity="warning" if age is None or age>=thresholds.pending_seconds else "info"
        findings.append(Finding("Pending" if severity=="warning" else "Starting",severity,evidence={"age_seconds":age}))
    elif phase=="Running":
        condition=next((c for c in (_value(status,"conditions") or []) if _value(c,"type")=="Ready"),None)
        unready=_value(condition,"status")=="False" or any(_value(cs,"ready") is False for cs,kind in _container_statuses(pod) if kind=="app")
        if unready:
            age=_age_seconds(_value(condition,"last_transition_time") if condition is not None else created,now)
            severity="warning" if age is None or age>=thresholds.ready_seconds else "info"
            findings.append(Finding("NotReady" if severity=="warning" else "Starting",severity,evidence={"age_seconds":age}))
    ranks={"error":0,"warning":1,"info":2}
    findings.sort(key=lambda finding:(ranks[finding.severity],0 if finding.code.startswith("Pod") else 1,finding.code,finding.container or ""))
    return findings


def summarize_issue(findings):
    for finding in findings:
        if finding.severity=="info":
            continue
        if finding.code in {"PodFailed","PodUnknown"}:
            return "phase="+finding.evidence["phase"]
        state=finding.evidence.get("state")
        if state in {"waiting","terminated"}:
            return state+"="+finding.code
        if finding.code=="RestartCount":
            return "restart_count>="+str(finding.evidence["threshold"])
        return finding.code
    return None


def select_problem_container(pod, findings):
    return next((finding.container for finding in findings if finding.container and finding.severity!="info"),None)


def _pod_issue(pod, restart_threshold):
    thresholds=Thresholds().model_copy(update={"pod_restart_count":max(restart_threshold,1)})
    findings=evaluate_pod(pod,thresholds=thresholds,now=_now_utc())
    if restart_threshold<=0:findings=[f for f in findings if f.code!="RestartCount"]
    return summarize_issue(findings)



def _validate_page_size(limit):
    if not isinstance(limit,int) or isinstance(limit,bool) or not 1<=limit<=200:
        raise K8sOperationError("INVALID_INPUT", "Page/result limit must be between 1 and 200")


def _collect_api_items(fn,**kwargs):
    gateway=_get_gateway()
    items=[]
    complete=True
    for page in gateway.iter_pages(fn,deadline=_DEADLINE.get() or gateway.clock()+gateway.settings.tool_timeout_seconds,**kwargs):
        items.extend(page.items)
        complete=complete and page.complete
    return items,complete


def _metrics_snapshot():
    gateway=_get_gateway()
    return gateway.fetch_metrics(deadline=_DEADLINE.get() or gateway.clock()+gateway.settings.tool_timeout_seconds)


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

    # metrics-server 可用时，再根据 usage/capacity 判断是否高负载（HighCPU/HighMemory）。
    metrics = _node_metric_view(None, node_name, snapshot)
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
        if cpu_ratio >= settings.thresholds.node_cpu_utilization:
            reasons.append("HighCPUUsage")
        if mem_ratio >= settings.thresholds.node_memory_utilization:
            reasons.append("HighMemoryUsage")

    return reasons, metric_detail


def _problem_reason_list(node, custom, cfg):
    return evaluate_node(node,_metrics_snapshot(),_get_gateway().settings)


def _event_brief(event: client.V1Event) -> dict[str, str]:
    """把 Event 压缩成短结构（原因/类型/消息/时间戳），用于 Pod 诊断输出。"""
    return {
        "reason": event.reason or "",
        "type": event.type or "",
        "message": (event.message or "")[:300],
        "last_timestamp": (
            str(getattr(event, "last_timestamp", "") or getattr(event, "event_time", "") or "")
        ),
    }


def _pod_issue_summary(pod, restart_threshold, thresholds=None):
    thresholds=thresholds or Thresholds().model_copy(update={"pod_restart_count":max(restart_threshold,1)})
    return summarize_issue(evaluate_pod(pod,thresholds=thresholds,now=_now_utc()))


def _pod_suggestions(issue: str | None, phase: str, findings=None) -> list[str]:
    """把常见 issue/phase 映射成可操作的排障建议（给 LLM 作为输出参考）。"""
    if findings and any(f.code=="Starting" for f in findings):
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


@mcp.tool(
    annotations={
        "title": "List Kubernetes Nodes",
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
@_k8s_tool
def list_nodes(label_selector: Annotated[str|None,Field(description="Optional Kubernetes label selector")]=None,
               limit: Annotated[int,Field(ge=1,le=200)]=100,continue_token: str|None=None) -> dict[str,Any]:
    _validate_page_size(limit)
    kube=_get_k8s_clients()
    page=_k8s_api_call(kube.core.list_node,label_selector=label_selector or None,limit=limit,_continue=continue_token)
    nodes, token=_page_parts(page)
    items=[_parse_node(node) for node in nodes]
    return {"cluster":kube.display_name,"count":len(items),"returned":len(items),"items":items,
            "continue_token":token,"coverage_scope":"page"}


@mcp.tool(
    annotations={
        "title": "Get Node Detail",
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
@_k8s_tool
def get_node_detail(
    node_name: Annotated[str, Field(description="节点名称")],
) -> dict[str, Any]:
    """获取单个节点的详细信息，包括基础属性、实时指标与节点上 Pod 概览。"""

    kube = _get_k8s_clients()
    node = _k8s_api_call(kube.core.read_node, node_name)
    pods = _active_pods_on_node(kube.core, node_name)
    metrics = _node_metric_view(kube.custom, node_name)
    detail = _parse_node(node)
    detail["pod_count"] = len(pods)
    detail["pods_sample"] = [_pod_basic_view(pod,_get_runtime_config().thresholds) for pod in pods[:20]]
    detail["metrics"] = metrics
    return detail


@mcp.tool(
    annotations={
        "title": "Audit Node Resource Usage",
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
@_k8s_tool
def get_node_resource_usage(
    node_name: Annotated[str, Field(description="节点名称")],
) -> dict[str, Any]:
    """
    审计节点上的 Pod Requests/Limits 水位，并在请求超过 100% 时给出 BestEffort Pod 迁移建议。
    """

    kube = _get_k8s_clients()
    cfg = _get_runtime_config()
    node = _k8s_api_call(kube.core.read_node, node_name)
    pods = _active_pods_on_node(kube.core, node_name)
    allocatable = node.status.allocatable if node.status else {}
    capacity = node.status.capacity if node.status else {}

    total_cpu_req_m = 0
    total_cpu_lim_m = 0
    total_mem_req_b = 0
    total_mem_lim_b = 0
    pod_breakdown: list[dict[str, Any]] = []
    best_effort_pods: list[dict[str, str]] = []

    for pod in pods:
        # 逐 Pod 汇总 requests/limits，并保留有限数量的 breakdown 供 LLM 下钻。
        pod_res = _pod_effective_resources(pod)
        total_cpu_req_m += pod_res.cpu_request_m
        total_cpu_lim_m += pod_res.cpu_limit_m
        total_mem_req_b += pod_res.memory_request_bytes
        total_mem_lim_b += pod_res.memory_limit_bytes

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
                    "cpu_limit_m": pod_res.cpu_limit_m,
                    "memory_request_bytes": pod_res.memory_request_bytes,
                    "memory_limit_bytes": pod_res.memory_limit_bytes,
                    "restart_count": _pod_restart_count(pod),
                }
            )

    alloc_cpu_m = _cpu_to_millicores(str(allocatable.get("cpu", "0")))
    alloc_mem_b = _memory_to_bytes(str(allocatable.get("memory", "0")))
    cap_cpu_m = _cpu_to_millicores(str(capacity.get("cpu", "0")))
    cap_mem_b = _memory_to_bytes(str(capacity.get("memory", "0")))

    cpu_req_ratio = _ratio(total_cpu_req_m, alloc_cpu_m)
    mem_req_ratio = _ratio(total_mem_req_b, alloc_mem_b)
    cpu_lim_ratio = _ratio(total_cpu_lim_m, alloc_cpu_m)
    mem_lim_ratio = _ratio(total_mem_lim_b, alloc_mem_b)

    metric_view = _node_metric_view(kube.custom, node_name)
    eviction_suggestion = None
    requests_overcommit = (
        cpu_req_ratio > cfg.thresholds.requests_overcommit_ratio
        or mem_req_ratio > cfg.thresholds.requests_overcommit_ratio
    )
    limits_overcommit = (
        cpu_lim_ratio > cfg.thresholds.limits_overcommit_ratio
        or mem_lim_ratio > cfg.thresholds.limits_overcommit_ratio
    )
    limits_warning = None
    if limits_overcommit:
        limits_warning = {
            "reason": "节点 Limits 水位偏高（软风险），建议结合实际负载与 QoS 分布评估是否需要扩容或做工作负载重平衡。",
            "threshold": cfg.thresholds.limits_overcommit_ratio,
        }

    if requests_overcommit:
        eviction_suggestion = {
            "reason": "节点已出现 requests overcommit，建议优先评估 BestEffort Pod 的迁移或驱逐影响。",
            "best_effort_candidates": best_effort_pods,
            "threshold": cfg.thresholds.requests_overcommit_ratio,
        }

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


@mcp.tool(
    annotations={
        "title": "Find Problem Nodes",
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
@_k8s_tool
def find_problem_nodes(label_selector: Annotated[str|None,Field(description="Optional node label filter")]=None,
                       max_results: Annotated[int,Field(ge=1,le=200)]=100) -> dict[str,Any]:
    _validate_page_size(max_results)
    kube=_get_k8s_clients()
    cfg=_get_runtime_config()
    nodes,scan_complete=_collect_api_items(kube.core.list_node,label_selector=label_selector or None)
    snapshot=_metrics_snapshot()
    items=[]
    for node in nodes:
        reasons,details=evaluate_node(node,snapshot,_get_gateway().settings)
        if reasons:
            items.append({"name":_safe_name(node),"status":"Ready" if _get_conditions(node).get("Ready")=="True" else "NotReady",
                          "internal_ip":_get_node_addresses(node).get("InternalIP",""),"age":_format_age(getattr(node.metadata,"creation_timestamp",None)),
                          "reasons":reasons,"conditions":_get_conditions(node),"metrics":details})
    ranks={"NodeNotReady":0,"DiskPressure":1,"MemoryPressure":1,"PIDPressure":1,"NetworkUnavailable":1,"HighCPUUsage":2,"HighMemoryUsage":2,"Unschedulable":3}
    items.sort(key=lambda item:(min(ranks.get(reason,4) for reason in item["reasons"]),item["name"]))
    truncated=len(items)>max_results
    if truncated:_mark_incomplete("Problem-node output truncated; narrow the label selector")
    return {"cluster":kube.display_name,"count":len(items[:max_results]),"items":items[:max_results],
            "thresholds":{"node_cpu_utilization":cfg.thresholds.node_cpu_utilization,"node_memory_utilization":cfg.thresholds.node_memory_utilization},
            "scanned_count":len(nodes),"matched_count":len(items) if scan_complete and snapshot.complete else None,
            "output_truncated":truncated,"complete":scan_complete and snapshot.complete and not truncated,"coverage_scope":"scan"}


@mcp.tool(
    annotations={
        "title": "List Node Pods",
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
@_k8s_tool
def list_node_pods(node_name: Annotated[str,Field(description="Node name")],
                   limit: Annotated[int,Field(ge=1,le=200)]=100,continue_token: str|None=None) -> dict[str,Any]:
    _validate_page_size(limit)
    kube=_get_k8s_clients()
    page=_k8s_api_call(kube.core.list_pod_for_all_namespaces,field_selector=f"spec.nodeName={node_name}",limit=limit,_continue=continue_token)
    pods, token=_page_parts(page)
    threshold=_get_runtime_config().thresholds.pod_restart_count
    items=[_pod_basic_view(pod,_get_runtime_config().thresholds) for pod in pods
           if getattr(pod.status,"phase",None) not in {"Succeeded","Failed"}]
    return {"cluster":kube.display_name,"node":node_name,"count":len(items),"returned":len(items),
            "items":items,"continue_token":token,"coverage_scope":"page"}


def _find_problem_container_name(pod):
    return select_problem_container(pod,evaluate_pod(pod,thresholds=_get_runtime_config().thresholds,now=_now_utc()))


def _read_pod_log_excerpt(
    core: client.CoreV1Api,
    namespace: str,
    pod_name: str,
    container_name: str | None,
) -> str:
    """读取 Pod 日志的短摘录，优先 previous=True 以覆盖 CrashLoopBackOff 场景。"""
    if not container_name:
        return ""

    for previous in (True, False):
        try:
            return _k8s_api_call(
                core.read_namespaced_pod_log,
                name=pod_name,
                namespace=namespace,
                container=container_name,
                previous=previous,
                tail_lines=80,
                timestamps=True,
            )[:4000]
        except ApiException:
            continue
    return ""


@mcp.tool(
    annotations={
        "title": "Diagnose Pod Issues",
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
@_k8s_tool
def diagnose_pod_issues(
    pod_name: Annotated[str, Field(description="Pod 名称")],
    namespace: Annotated[str, Field(description="Pod 所在命名空间")],
) -> dict[str, Any]:
    """
    对指定 Pod 做精简版诊断：
    - Pending：补充事件信息
    - CrashLoopBackOff / ImagePullBackOff 等：尝试读取 previous 日志
    """

    kube = _get_k8s_clients()
    cfg = _get_runtime_config()
    pod = _k8s_api_call(kube.core.read_namespaced_pod, name=pod_name, namespace=namespace)

    # issue 先识别明显异常，再识别“重启偏高”，这样输出更符合排障优先级。
    phase = pod.status.phase if pod.status else ""
    findings = evaluate_pod(pod,thresholds=cfg.thresholds,now=_now_utc())
    issue = summarize_issue(findings)
    problem_container = select_problem_container(pod,findings)

    # Pending/CrashLoopBackOff 等场景下，事件信息对定位非常关键，这里做采样返回。
    events = _k8s_api_call(
        kube.core.list_namespaced_event,
        namespace=namespace,
        field_selector=f"involvedObject.kind=Pod,involvedObject.name={pod_name}",
    ).items

    log_excerpt = ""
    if issue and (
        "CrashLoopBackOff" in issue
        or "ImagePullBackOff" in issue
        or "ErrImagePull" in issue
        or "OOMKilled" in issue
    ):
        log_excerpt = _read_pod_log_excerpt(kube.core, namespace, pod_name, problem_container)

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
        "events": [_event_brief(event) for event in events[:20]],
        "log_excerpt": log_excerpt,
        "findings": [asdict(f) for f in findings],
        "suggestions": _pod_suggestions(issue, phase, findings),
    }


@mcp.tool(
    annotations={
        "title": "Health Check",
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
@_k8s_tool
def health_check() -> dict[str,Any]:
    kube=_get_k8s_clients()
    _k8s_api_call(kube.core.list_node,limit=1)
    return {"status":"healthy","cluster":kube.display_name,"node_count":None,
            "warnings":["Connectivity check does not count all nodes"]}


@mcp.resource("k8s://cluster/summary")
@_k8s_tool
def cluster_summary() -> dict[str,Any]:
    kube=_get_k8s_clients()
    nodes,complete=_collect_api_items(kube.core.list_node)
    snapshot=_metrics_snapshot()
    problems=sum(bool(evaluate_node(node,snapshot,_get_gateway().settings)[0]) for node in nodes)
    return {"cluster":kube.display_name,"node_count":len(nodes) if complete else None,"scanned_count":len(nodes),
            "problem_node_count":problems if complete and snapshot.complete else None,"observed_problem_node_count":problems,
            "complete":complete and snapshot.complete,"coverage_scope":"scan",
            "available_tools":["list_nodes","get_node_detail","get_node_resource_usage","find_problem_nodes","list_node_pods","diagnose_pod_issues","health_check"]}


@mcp.prompt
def node_triage_prompt(
    node_name: Annotated[str, Field(description="待排查的节点名称")],
) -> str:
    """给 LLM 的节点排障 SOP。"""

    return f"""
你是一名 Kubernetes SRE，请围绕节点 `{node_name}` 按以下顺序进行排障：

1. 先调用 `get_node_detail` 查看节点状态、条件、污点、基础信息和节点上的 Pod 概览。
2. 再调用 `get_node_resource_usage` 检查 allocatable、requests/limits 水位以及是否存在 BestEffort Pod 迁移建议。
3. 如果节点上有异常 Pod，再调用 `list_node_pods` 筛出异常项，并按需继续调用 `diagnose_pod_issues`。
4. 输出结论时请分别回答：
   - 节点是否真的异常，还是只是高负载/被主动 cordon；
   - 影响面主要在哪些 Pod/工作负载上；
   - 建议的下一步排查或缓解动作。
"""


@mcp.prompt
def cluster_problem_scan_prompt() -> str:
    """给 LLM 的集群问题扫描 SOP。"""

    return """
你是一名 Kubernetes 巡检助手，请按以下步骤工作：

1. 先读取 `k8s://cluster/summary` 建立整体上下文。
2. 调用 `find_problem_nodes` 找出异常节点，并按严重程度排序：
   - NodeNotReady
   - DiskPressure / MemoryPressure / PIDPressure
   - HighCPUUsage / HighMemoryUsage
3. 对每个异常节点：
   - 用 `get_node_detail` 看详细状态；
   - 用 `get_node_resource_usage` 看资源审计和 overcommit；
   - 必要时用 `list_node_pods` / `diagnose_pod_issues` 下钻。
4. 最终输出请包含：
   - 问题节点列表
   - 每个节点的关键异常原因
   - 是否需要优先处理某些 Pod/工作负载
   - 建议的处置优先级
"""


def main(argv=None, *, default_transport="sse"):
    settings = load_settings(os.environ)
    parser = argparse.ArgumentParser(description="AstroMCP read-only Kubernetes inspection")
    parser.add_argument("--transport",choices=("stdio","http","sse"),default=os.environ.get("MCP_TRANSPORT",default_transport))
    parser.add_argument("--host",default=settings.bind_host)
    parser.add_argument("--port",type=int,default=settings.bind_port)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    if args.transport != "stdio":
        if args.host != "localhost" and not ipaddress.ip_address(args.host).is_loopback:
            raise ValueError("Only loopback HTTP/SSE is supported")
        mcp.run(transport=args.transport,host=args.host,port=args.port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
