from __future__ import annotations

import ipaddress
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

HOST_FORBIDDEN = re.compile(r"[;&|`$(){}\[\]<>\"']")


class HostSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    tool_timeout_seconds: float = Field(default=30, gt=0)
    queue_timeout_seconds: float = Field(default=2, ge=0)
    ssh_connect_timeout_seconds: float = Field(default=8, gt=0)
    ssh_command_timeout_seconds: float = Field(default=15, gt=0)
    ssh_max_in_flight: int = Field(default=4, ge=1)
    ssh_max_per_target: int = Field(default=2, ge=1)
    ssh_output_limit_bytes: int = Field(default=262144, ge=1)
    ssh_known_hosts: str | None = None
    disk_threshold: int = Field(default=85, ge=0, le=100)
    transport: Literal["stdio", "http", "sse"] = "stdio"
    bind_host: str = "127.0.0.1"
    bind_port: int = Field(default=8081, ge=1, le=65535)

    @model_validator(mode="after")
    def validate_queue(self):
        if self.queue_timeout_seconds >= self.tool_timeout_seconds:
            raise ValueError("queue timeout must be less than tool timeout")
        return self


def load_settings(environ: Mapping[str, str], config_path: Path | None = None) -> HostSettings:
    path = config_path or (Path(environ["MCP_CONFIG"]) if environ.get("MCP_CONFIG") else None)
    data = json.loads(path.read_text(encoding="utf-8")) if path else {}
    if not isinstance(data, dict):
        raise ValueError("configuration must be an object")
    names = {
        "MCP_TOOL_TIMEOUT_SECONDS": "tool_timeout_seconds",
        "MCP_QUEUE_TIMEOUT_SECONDS": "queue_timeout_seconds",
        "MCP_SSH_CONNECT_TIMEOUT_SECONDS": "ssh_connect_timeout_seconds",
        "MCP_SSH_COMMAND_TIMEOUT_SECONDS": "ssh_command_timeout_seconds",
        "MCP_SSH_MAX_IN_FLIGHT": "ssh_max_in_flight",
        "MCP_SSH_MAX_PER_TARGET": "ssh_max_per_target",
        "MCP_SSH_OUTPUT_LIMIT_BYTES": "ssh_output_limit_bytes",
        "MCP_SSH_KNOWN_HOSTS": "ssh_known_hosts",
        "MCP_DISK_THRESHOLD": "disk_threshold",
        "MCP_TRANSPORT": "transport",
        "MCP_BIND_HOST": "bind_host",
        "MCP_BIND_PORT": "bind_port",
    }
    data.update({field: environ[key] for key, field in names.items() if key in environ})
    return HostSettings.model_validate(data)


def _check_host(host: str) -> str | None:
    """校验主机名/IP，合法返回 None，否则返回错误信息"""
    if not host or not host.strip():
        return "host 不能为空"
    if HOST_FORBIDDEN.search(host) or any(c.isspace() for c in host) or host.startswith("-"):
        return f"host 包含非法字符: {host}"
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if len(host) > 253 or not all(
            re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9_-]{0,61}[A-Za-z0-9])?", label)
            for label in host.rstrip(".").split(".")
        ):
            return "Invalid hostname or IP"
    return None


def _check_port(port: int) -> str | None:
    """校验端口号，合法返回 None，否则返回错误信息"""
    if not isinstance(port, int) or port < 1 or port > 65535:
        return f"port 超出范围 1-65535: {port}"
    return None


def _check_path(path: str) -> str | None:
    """校验路径，合法返回 None，否则返回错误信息"""
    if not path or not path.strip():
        return "path 不能为空"
    if any(c in path for c in "\r\n\0"):
        return f"path 包含非法字符: {path}"
    return None


def is_local(host: str) -> bool:
    """判断给定的 host 是否指代本机"""
    return host in ("127.0.0.1", "localhost", "0.0.0.0", "::1")
