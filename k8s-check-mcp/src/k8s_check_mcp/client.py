from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
from urllib3.exceptions import HTTPError, ReadTimeoutError

from .config import K8sSettings, load_settings
from .limiter import TokenBucketLimiter
from .models import (
    EvidenceResult,
    K8sOperationError,
    MCPKubeClients,
    MetricsSnapshot,
    PageResult,
    _mark_incomplete,
    _value,
)
from .presentation import _event_brief, _safe_name, redact_common_secrets


def init_k8s_client(settings=None) -> MCPKubeClients:
    settings = settings or load_settings(os.environ)
    cfg = client.Configuration()
    if settings.in_cluster:
        config.load_incluster_config(client_configuration=cfg)
    else:
        config.load_kube_config(
            config_file=settings.kubeconfig, context=settings.context, client_configuration=cfg
        )
    cfg.verify_ssl = settings.verify_ssl
    cfg.retries = 0
    cfg.connection_pool_maxsize = settings.api_max_in_flight
    api_client = client.ApiClient(cfg)
    return MCPKubeClients(
        "in-cluster" if settings.in_cluster else settings.context or "kubeconfig",
        api_client,
        client.CoreV1Api(api_client),
        client.CustomObjectsApi(api_client),
    )


def _page_parts(page):
    if isinstance(page, dict):
        return (page.get("items", []), page.get("metadata", {}).get("continue"))
    return (page.items, getattr(getattr(page, "metadata", None), "_continue", None))


class K8sGateway:
    def __init__(self, settings: K8sSettings, *, client_factory=None, clock=time.monotonic):
        self.settings, self.clock = (settings, clock)
        self._factory = client_factory or (lambda: init_k8s_client(settings))
        self._clients = None
        self._lock = threading.Lock()
        self._closed = False
        self.limiter = TokenBucketLimiter(
            settings.api_rps,
            settings.api_burst,
            settings.api_max_in_flight,
            queue_timeout=settings.queue_timeout_seconds,
            enabled=settings.rate_limit_enabled,
            clock=clock,
        )

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
            remaining = deadline - self.clock()
            if remaining <= 0:
                raise K8sOperationError("TIMEOUT", "Operation deadline exceeded")
            kwargs["_request_timeout"] = (
                min(self.settings.connect_timeout_seconds, remaining),
                min(self.settings.read_timeout_seconds, remaining),
            )
            name = getattr(fn, "__name__", "")
            preserve = name in {"read_namespaced_pod", "list_pod_for_all_namespaces"}
            if preserve:
                kwargs["_preload_content"] = False
            response = fn(*args, **kwargs)
            if preserve and isinstance(getattr(response, "data", None), (bytes, str)):
                try:
                    raw = json.loads(response.data)
                    result = self.clients.api_client.deserialize(
                        response, "V1Pod" if name == "read_namespaced_pod" else "V1PodList"
                    )
                    if name == "read_namespaced_pod":
                        result._astromcp_raw = raw
                    else:
                        for pod, item in zip(result.items, raw.get("items", [])):
                            pod._astromcp_raw = item
                    return result
                finally:
                    response.release_conn()
            return response
        except ApiException as exc:
            code = {
                401: "AUTH_FAILED",
                403: "PERMISSION_DENIED",
                404: "NOT_FOUND",
                429: "BUSY",
            }.get(exc.status, "COMMAND_FAILED")
            raise K8sOperationError(code, "Kubernetes API request failed") from exc
        except (TimeoutError, ReadTimeoutError):
            raise K8sOperationError("TIMEOUT", "Kubernetes API timed out") from None
        except (OSError, HTTPError):
            raise K8sOperationError("UNREACHABLE", "Kubernetes API unavailable") from None
        finally:
            self.limiter.release()

    def iter_pages(self, fn, *, deadline, **kwargs):
        token, seen, count = (None, set(), 0)
        while True:
            try:
                page = self.call(
                    fn, deadline=deadline, limit=self.settings.page_size, _continue=token, **kwargs
                )
            except K8sOperationError as exc:
                _mark_incomplete(exc.code)
                yield PageResult([], None, False, [exc.code])
                return
            items, next_token = _page_parts(page)
            if next_token and next_token in seen:
                _mark_incomplete("Repeated API continuation token")
                yield PageResult([], None, False, ["Repeated API continuation token"])
                return
            remaining = self.settings.max_objects - count
            if len(items) > remaining or (len(items) == remaining and next_token):
                _mark_incomplete("API object limit reached")
                yield PageResult(list(items[:remaining]), None, False, ["API object limit reached"])
                return
            yield PageResult(list(items), next_token)
            count += len(items)
            if not next_token:
                return
            seen.add(next_token)
            token = next_token

    def fetch_metrics(self, *, deadline):
        observed = datetime.now(timezone.utc)
        try:
            obj = self.call(
                self.clients.custom.list_cluster_custom_object,
                deadline=deadline,
                group="metrics.k8s.io",
                version="v1beta1",
                plural="nodes",
            )
        except K8sOperationError as exc:
            _mark_incomplete("Node metrics unavailable: " + exc.code)
            return MetricsSnapshot({}, observed, False, exc.code, False, [exc.code])
        if not isinstance(obj, dict) or not isinstance(obj.get("items"), list):
            _mark_incomplete("Node metrics malformed")
            return MetricsSnapshot({}, observed, False, "PARSE_ERROR", False, ["PARSE_ERROR"])
        usage = {}
        complete = len(obj["items"]) <= self.settings.max_objects
        for item in obj["items"][: self.settings.max_objects]:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("metadata"), dict)
                or (not isinstance(item.get("usage"), dict))
            ):
                complete = False
                continue
            name = item["metadata"].get("name")
            if isinstance(name, str):
                usage[name] = item["usage"]
        if not complete:
            _mark_incomplete("Node metrics incomplete or object limit reached")
        return MetricsSnapshot(
            usage, observed, True, None, complete, [] if complete else ["Metrics incomplete"]
        )

    def close(self):
        with self._lock:
            self._closed = True
            if self._clients is not None:
                self._clients.api_client.close()


def _event_sort_key(event):
    value = (
        _value(event, "last_timestamp")
        or _value(event, "event_time")
        or _value(event, "first_timestamp")
    )
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            value = None
    stamp = (
        value.timestamp()
        if isinstance(value, datetime) and value.tzinfo is not None
        else float("-inf")
    )
    return (stamp, _safe_name(event))


def read_events(gateway, *, namespace: str, pod_uid: str, deadline: float) -> EvidenceResult:
    if not pod_uid:
        return EvidenceResult([], False, ["Pod UID unavailable; events were not queried by name"])
    events = []
    warnings = []
    complete = True
    for page in gateway.iter_pages(
        gateway.clients.core.list_namespaced_event,
        deadline=deadline,
        namespace=namespace,
        field_selector=f"involvedObject.kind=Pod,involvedObject.uid={pod_uid}",
    ):
        events.extend(
            event
            for event in page.items
            if _value(_value(event, "involved_object"), "uid") == pod_uid
        )
        complete = complete and page.complete
        warnings.extend(page.warnings)
    events.sort(key=_event_sort_key, reverse=True)
    if len(events) > 20:
        complete = False
        warnings.append("Events truncated to the latest 20 records")
    return EvidenceResult([_event_brief(event) for event in events[:20]], complete, warnings)


def read_log_excerpt(
    gateway, *, namespace: str, pod_name: str, container_name: str, deadline: float
) -> EvidenceResult:
    if not gateway.settings.include_logs:
        return EvidenceResult("", False, ["Log collection disabled by configuration"])
    warnings = []
    for previous in (True, False):
        try:
            text = gateway.call(
                gateway.clients.core.read_namespaced_pod_log,
                deadline=deadline,
                name=pod_name,
                namespace=namespace,
                container=container_name,
                previous=previous,
                tail_lines=80,
                timestamps=True,
                limit_bytes=16384,
            )
        except K8sOperationError as exc:
            warnings.append(
                ("Previous" if previous else "Current") + " logs unavailable: " + exc.code
            )
            if exc.code in {"PERMISSION_DENIED", "AUTH_FAILED", "TIMEOUT", "BUSY"}:
                break
            continue
        if not isinstance(text, str):
            return EvidenceResult("", False, warnings + ["Unexpected log response type"])
        truncated = len(text) > 4000 or len(text.encode("utf-8")) >= 16384
        if truncated:
            warnings.append("Log excerpt truncated")
        return EvidenceResult(redact_common_secrets(text)[:4000], not warnings, warnings)
    return EvidenceResult("", False, warnings)
