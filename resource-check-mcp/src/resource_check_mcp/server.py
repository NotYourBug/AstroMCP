from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from functools import wraps
from inspect import signature
from typing import Annotated
from uuid import uuid4

import psutil
from fastmcp import FastMCP
from pydantic import Field

from . import collectors
from .collectors.local import LocalCollector
from .config import HostSettings, _check_host, _check_path, _check_port, is_local, load_settings
from .models import _REQUEST_ID, _TOOL_DEADLINE, HostOperationError, SSHConnection, _err, _ok
from .probes import probe_ping, probe_tcp
from .ssh import SSHExecutor, detect_remote_os

DEFAULT_DISK_THRESHOLD = 85
logger = logging.getLogger("astromcp.host")


def _operation(fn):
    @wraps(fn)
    async def wrapped(self, *args, **kwargs):
        token = _TOOL_DEADLINE.set(time.monotonic() + self.settings.tool_timeout_seconds)
        request = _REQUEST_ID.set(str(uuid4()))
        host = kwargs.get("host", kwargs.get("hostname", "127.0.0.1"))
        try:
            return await fn(self, *args, **kwargs)
        except HostOperationError as exc:
            return _err(host, str(exc), error_code=exc.code)
        except (TimeoutError, asyncio.TimeoutError):
            return _err(host, "Collection deadline exceeded", error_code="TIMEOUT")
        except (PermissionError, psutil.AccessDenied):
            return _err(host, "Insufficient inspection permission", error_code="PERMISSION_DENIED")
        except FileNotFoundError:
            return _err(host, "Inspection path not found", error_code="NOT_FOUND")
        except ValueError as exc:
            return _err(host, str(exc), error_code="INVALID_INPUT")
        finally:
            logger.info("tool=%s request_id=%s target=%s", fn.__name__, _REQUEST_ID.get(), host)
            _TOOL_DEADLINE.reset(token)
            _REQUEST_ID.reset(request)

    return wrapped


class HostService:
    def __init__(self, settings, executor=None):
        self.settings = settings
        self.executor = executor or SSHExecutor(settings)
        self.local = LocalCollector(settings)

    async def _inspect(
        self, metric, host, port, username, password, key_path, path="/", threshold=85
    ):
        error = _check_host(host) or _check_port(port)
        if error:
            raise ValueError(error)
        if metric == "disk":
            if _check_path(path) or not 0 <= threshold <= 100:
                raise ValueError("Invalid path or disk threshold")
        if is_local(host):
            fn = getattr(self.local, f"collect_{metric}")
            data = await fn(path, threshold) if metric == "disk" else await fn()
            return _ok(host=host, **data)
        connection = SSHConnection(host, port, username or "", password, key_path)
        deadline = _TOOL_DEADLINE.get()
        os_type = await detect_remote_os(connection, self.executor, deadline)
        fn = getattr(collectors, f"collect_{metric}")
        if metric == "disk":
            return await fn(os_type, connection, self.executor, deadline, path, threshold)
        return await fn(os_type, connection, self.executor, deadline)

    @_operation
    async def check_host_status(
        self, hostname: Annotated[str, Field(description="主机名或 IP 地址")]
    ) -> dict:
        return await probe_ping(hostname)

    @_operation
    async def check_disk_usage(
        self,
        host: Annotated[
            str, Field(description="目标主机 IP 或域名（本机传 127.0.0.1）)")
        ] = "127.0.0.1",
        path: Annotated[str, Field(description="要检查的磁盘路径（如 / 或 C:）")] = "/",
        threshold: Annotated[
            int,
            Field(ge=0, le=100, description=f"磁盘使用率告警阈值，默认 {DEFAULT_DISK_THRESHOLD}%"),
        ] = DEFAULT_DISK_THRESHOLD,
        username: Annotated[str | None, Field(description="SSH 用户名（仅远程需要）")] = None,
        password: Annotated[str | None, Field(description="SSH 密码（仅远程需要）")] = None,
        key_path: Annotated[str | None, Field(description="SSH 私钥路径（仅远程需要）")] = None,
        port: Annotated[int, Field(description="SSH 端口")] = 22,
    ) -> dict:
        return await self._inspect(
            "disk", host, port, username, password, key_path, path, threshold
        )

    @_operation
    async def check_mem(
        self,
        host: Annotated[str, Field(description="目标主机 IP 或域名")] = "127.0.0.1",
        username: Annotated[str | None, Field(description="SSH 用户名")] = None,
        password: Annotated[str | None, Field(description="SSH 密码")] = None,
        key_path: Annotated[str | None, Field(description="SSH 私钥路径（仅远程需要）")] = None,
        port: Annotated[int, Field(description="SSH 端口")] = 22,
    ) -> dict:
        return await self._inspect("memory", host, port, username, password, key_path)

    @_operation
    async def check_cpu(
        self,
        host: Annotated[str, Field(description="目标主机 IP 或域名")] = "127.0.0.1",
        username: Annotated[str | None, Field(description="SSH 用户名")] = None,
        password: Annotated[str | None, Field(description="SSH 密码")] = None,
        key_path: Annotated[str | None, Field(description="SSH 私钥路径（仅远程需要）")] = None,
        port: Annotated[int, Field(description="SSH 端口")] = 22,
    ) -> dict:
        return await self._inspect("cpu", host, port, username, password, key_path)

    @_operation
    async def check_tcp(
        self,
        host: Annotated[str, Field(description="目标主机 IP 或域名")] = "127.0.0.1",
        username: Annotated[str | None, Field(description="SSH 用户名")] = None,
        password: Annotated[str | None, Field(description="SSH 密码")] = None,
        key_path: Annotated[str | None, Field(description="SSH 私钥路径（仅远程需要）")] = None,
        port: Annotated[int, Field(description="SSH 端口")] = 22,
    ) -> dict:
        return await self._inspect("tcp", host, port, username, password, key_path)

    @_operation
    async def check_service_port(
        self,
        host: Annotated[str, Field(description="主机名或 IP 地址")],
        port: Annotated[int, Field(description="端口号")],
    ) -> dict:
        return await probe_tcp(host, port)

    async def aclose(self):
        await self.executor.aclose()
        await self.local.aclose()


def create_server(
    settings: HostSettings | None = None, *, executor: SSHExecutor | None = None
) -> FastMCP:
    settings = settings or load_settings(os.environ)
    service = HostService(settings, executor)

    @asynccontextmanager
    async def lifespan(server):
        try:
            yield {}
        finally:
            await service.aclose()

    mcp = FastMCP("sre-ops-server", lifespan=lifespan)
    for name in (
        "check_host_status",
        "check_disk_usage",
        "check_mem",
        "check_cpu",
        "check_tcp",
        "check_service_port",
    ):
        tool = (
            _configured_disk_tool(service) if name == "check_disk_usage" else getattr(service, name)
        )
        mcp.tool(name=name, annotations={"readOnlyHint": True, "openWorldHint": True})(tool)
    return mcp


def _configured_disk_tool(service):
    """Publish the instance default while preserving an explicit threshold, including 85."""
    original = service.check_disk_usage

    @wraps(original)
    async def tool(**kwargs):
        kwargs.setdefault("threshold", service.settings.disk_threshold)
        return await original(**kwargs)

    sig = signature(original)
    tool.__signature__ = sig.replace(
        parameters=[
            param.replace(default=service.settings.disk_threshold)
            if param.name == "threshold"
            else param
            for param in sig.parameters.values()
        ]
    )
    return tool
