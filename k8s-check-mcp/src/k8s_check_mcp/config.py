from __future__ import annotations

import json
from pathlib import Path
from collections.abc import Mapping
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

class Thresholds(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, allow_inf_nan=False)
    node_cpu_utilization: float = Field(default=0.8, ge=0, le=1)
    node_memory_utilization: float = Field(default=0.85, ge=0, le=1)
    pod_restart_count: int = Field(default=3, ge=1)
    requests_overcommit_ratio: float = Field(default=1, gt=0)
    limits_overcommit_ratio: float = Field(default=1.5, gt=0)
    pending_seconds: float = Field(default=300, gt=0)
    ready_seconds: float = Field(default=60, gt=0)

class K8sSettings(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, allow_inf_nan=False)
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
    transport: Literal['stdio', 'http', 'sse'] = 'stdio'
    bind_host: str = '127.0.0.1'
    bind_port: int = Field(default=8080, ge=1, le=65535)

    @model_validator(mode='after')
    def validate_budget(self):
        if self.queue_timeout_seconds >= self.tool_timeout_seconds:
            raise ValueError('Queue budget must be less than tool budget')
        return self

def load_settings(environ: Mapping[str, str], config_path: Path | None=None) -> K8sSettings:
    path = config_path or (Path(environ['MCP_CONFIG']) if environ.get('MCP_CONFIG') else None)
    data = {}
    if path:
        raw = path.read_text(encoding='utf-8')
        if path.suffix.lower() in {'.yaml', '.yml'}:
            try:
                import yaml
            except ImportError as exc:
                raise ValueError('YAML requires astromcp-k8s[yaml]') from exc
            try:
                data = yaml.safe_load(raw)
            except yaml.YAMLError as exc:
                raise ValueError('Invalid YAML configuration') from exc
        else:
            data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError('Configuration must be an object')
    threshold_data = dict(data.get('thresholds', {}))
    for name in Thresholds.model_fields:
        key = 'MCP_THRESHOLD_' + name.upper()
        if key in environ:
            threshold_data[name] = environ[key]
    data['thresholds'] = threshold_data
    names = {'MCP_K8S_IN_CLUSTER': 'in_cluster', 'MCP_K8S_KUBECONFIG': 'kubeconfig', 'MCP_K8S_CONTEXT': 'context', 'MCP_K8S_VERIFY_SSL': 'verify_ssl', 'MCP_K8S_CONNECT_TIMEOUT_SECONDS': 'connect_timeout_seconds', 'MCP_K8S_READ_TIMEOUT_SECONDS': 'read_timeout_seconds', 'MCP_TOOL_TIMEOUT_SECONDS': 'tool_timeout_seconds', 'MCP_QUEUE_TIMEOUT_SECONDS': 'queue_timeout_seconds', 'MCP_K8S_API_RPS': 'api_rps', 'MCP_K8S_API_BURST': 'api_burst', 'MCP_K8S_API_MAX_IN_FLIGHT': 'api_max_in_flight', 'MCP_K8S_RATE_LIMIT_ENABLED': 'rate_limit_enabled', 'MCP_K8S_PAGE_SIZE': 'page_size', 'MCP_K8S_MAX_OBJECTS': 'max_objects', 'MCP_K8S_INCLUDE_LOGS': 'include_logs', 'MCP_TRANSPORT': 'transport', 'MCP_BIND_HOST': 'bind_host', 'MCP_BIND_PORT': 'bind_port'}
    data.update({field: environ[key] for key, field in names.items() if key in environ})
    return K8sSettings.model_validate(data)
