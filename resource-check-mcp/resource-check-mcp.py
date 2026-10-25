"""
SRE Operations MCP Server — 系统资源监控工具集

基于 FastMCP 构建，提供给 LLM 使用的运维监控工具。
支持本地及跨主机(SSH)的磁盘、内存、CPU、TCP 连接监控，以及端口和主机连通性检测。
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import logging
import math
import os
import platform
import re
import shlex
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
from uuid import uuid4
from pathlib import Path
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Annotated, Optional

import paramiko
import psutil
from fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field, model_validator

# ==================== 日志配置 ====================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("sre-ops-server")

# ==================== 常量 ====================

DEFAULT_DISK_THRESHOLD = 85
SSH_CONNECT_TIMEOUT = 10
SSH_COMMAND_TIMEOUT = 30
TCP_CHECK_TIMEOUT = 5

# ==================== 初始化 ====================

mcp = FastMCP("sre-ops-server")


class HostSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    tool_timeout_seconds: float = Field(default=30, gt=0)
    queue_timeout_seconds: float = Field(default=2, ge=0)
    ssh_connect_timeout_seconds: float = Field(default=8, gt=0)
    ssh_command_timeout_seconds: float = Field(default=15, gt=0)
    ssh_max_in_flight: int = Field(default=4, ge=1)
    ssh_max_per_target: int = Field(default=2, ge=1)
    ssh_output_limit_bytes: int = Field(default=262144, ge=1)
    ssh_known_hosts: str | None = None
    disk_threshold: int = Field(default=85, ge=0, le=100)
    transport: str = "stdio"
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
        "MCP_TRANSPORT": "transport", "MCP_BIND_HOST": "bind_host", "MCP_BIND_PORT": "bind_port",
    }
    data.update({field: environ[key] for key, field in names.items() if key in environ})
    return HostSettings.model_validate(data)


def build_unix_disk_command(path: str) -> str:
    if not path.startswith("/") or any(c in path for c in "\r\n\0"):
        raise ValueError("Unix disk path must be absolute without control characters")
    return f"df -kP {shlex.quote(path)}"


def build_windows_disk_command(path: str) -> str:
    if not re.fullmatch(r"[A-Za-z]:(?:[\\/][^\r\n\0]*)?", path):
        raise ValueError("Windows disk path must have an explicit drive letter")
    drive = path[0].upper()
    return ('powershell -NoProfile -Command "'
            f'Get-Volume -DriveLetter {drive} | Select-Object Size, SizeRemaining | ConvertTo-Json"')


def create_ssh_client(settings: HostSettings):
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    if settings.ssh_known_hosts:
        client.load_host_keys(settings.ssh_known_hosts)
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    return client


@dataclass(frozen=True)
class SSHConnection:
    host: str
    port: int
    username: str
    password: str | None = field(default=None, repr=False)
    key_path: str | None = None


@dataclass
class CommandResult:
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    error_code: str | None = None
    complete: bool = True


class SSHExecutor:
    """Bounded blocking workers; a cancelled waiter never frees a running worker's lease."""

    def __init__(self, settings: HostSettings, *, client_factory=None, clock=time.monotonic):
        self.settings, self.clock = settings, clock
        self.client_factory = client_factory or (lambda: create_ssh_client(settings))
        self._pool = ThreadPoolExecutor(max_workers=settings.ssh_max_in_flight, thread_name_prefix="astromcp-ssh")
        self._sem = threading.BoundedSemaphore(settings.ssh_max_in_flight)
        self._lock = threading.Lock()
        self._targets = {}
        self._active = {}
        self._closed = False

    async def execute(self, connection: SSHConnection, command: str, *, deadline: float) -> CommandResult:
        if not connection.username:
            return CommandResult(error_code="INVALID_INPUT", complete=False)
        key = (connection.host, connection.port, connection.username)
        queue_end = min(deadline, self.clock() + self.settings.queue_timeout_seconds)
        while not self._closed:
            if self.clock() >= queue_end:
                return CommandResult(error_code="BUSY", complete=False)
            if self._sem.acquire(blocking=False):
                with self._lock:
                    entry = self._targets.setdefault(key, [threading.BoundedSemaphore(self.settings.ssh_max_per_target), 0])
                    if entry[0].acquire(blocking=False):
                        entry[1] += 1
                        break
                    if entry[1] == 0:
                        self._targets.pop(key, None)
                self._sem.release()
            await asyncio.sleep(.005)
        else:
            return CommandResult(error_code="BUSY", complete=False)
        stop, holder = threading.Event(), []
        future = self._pool.submit(self._run, connection, command, deadline, stop, holder)
        with self._lock:
            self._active[future] = (stop, holder)

        def finished(done):
            with self._lock:
                self._active.pop(done, None)
                entry[1] -= 1
                if entry[1] == 0:
                    self._targets.pop(key, None)
            entry[0].release()
            self._sem.release()

        future.add_done_callback(finished)
        wrapped = asyncio.wrap_future(future)
        try:
            return await asyncio.wait_for(asyncio.shield(wrapped), max(deadline-self.clock(), .001))
        except (TimeoutError, asyncio.TimeoutError):
            self._abort(stop, holder)
            return CommandResult(error_code="TIMEOUT", complete=False)
        except asyncio.CancelledError:
            self._abort(stop, holder)
            raise

    @staticmethod
    def _abort(stop, holder):
        stop.set()
        for client in holder:
            client.close()

    def _run(self, connection, command, deadline, stop, holder):
        client = channel = None
        out, err = bytearray(), bytearray()

        def remaining(end):
            value = end-self.clock()
            if stop.is_set() or value <= 0:
                raise TimeoutError
            return value

        try:
            client = self.client_factory()
            holder.append(client)
            timeout = min(remaining(deadline), self.settings.ssh_connect_timeout_seconds)
            client.connect(hostname=connection.host, port=connection.port, username=connection.username,
                           password=connection.password, key_filename=connection.key_path,
                           allow_agent=True, look_for_keys=True, timeout=timeout,
                           banner_timeout=timeout, auth_timeout=timeout)
            end = min(deadline, self.clock()+self.settings.ssh_command_timeout_seconds)
            stdin, stdout, _ = client.exec_command(command, timeout=remaining(end))
            stdin.close()
            channel = stdout.channel
            while True:
                remaining(end)
                for ready, recv, buffer in ((channel.recv_ready, channel.recv, out),
                                             (channel.recv_stderr_ready, channel.recv_stderr, err)):
                    if ready():
                        buffer.extend(recv(32768))
                        if len(out)+len(err) > self.settings.ssh_output_limit_bytes:
                            return CommandResult(error_code="OUTPUT_LIMIT", complete=False)
                if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                    code = channel.recv_exit_status()
                    return CommandResult(code, out.decode("utf-8", errors="replace").strip(),
                                         err.decode("utf-8", errors="replace").strip())
                time.sleep(.005)
        except paramiko.BadHostKeyException:
            return CommandResult(error_code="HOST_KEY_CHANGED", complete=False)
        except paramiko.AuthenticationException:
            return CommandResult(error_code="AUTH_FAILED", complete=False)
        except (TimeoutError, asyncio.TimeoutError):
            return CommandResult(error_code="TIMEOUT", complete=False)
        except paramiko.SSHException as exc:
            code = "HOST_KEY_UNKNOWN" if "known_hosts" in str(exc) else "UNREACHABLE"
            return CommandResult(error_code=code, complete=False)
        except OSError:
            return CommandResult(error_code="UNREACHABLE", complete=False)
        finally:
            if channel is not None:
                channel.close()
            if client is not None:
                client.close()

    async def aclose(self):
        self._closed = True
        with self._lock:
            active = list(self._active.items())
        for _, (stop, holder) in active:
            self._abort(stop, holder)
        if active:
            await asyncio.wait([asyncio.wrap_future(f) for f, _ in active], timeout=1)
        self._pool.shutdown(wait=False, cancel_futures=True)


_EXECUTOR = None
_TOOL_DEADLINE = ContextVar("host_deadline", default=None)
_REQUEST_ID = ContextVar("host_request_id", default=None)


def _host_tool(fn):
    @wraps(fn)
    async def wrapped(*args, **kwargs):
        token = _TOOL_DEADLINE.set(time.monotonic()+load_settings(os.environ).tool_timeout_seconds)
        request = _REQUEST_ID.set(str(uuid4()))
        try:
            return await fn(*args, **kwargs)
        except ValueError as exc:
            return _err(kwargs.get("host", ""), str(exc), error_code="INVALID_INPUT")
        finally:
            _TOOL_DEADLINE.reset(token)
            _REQUEST_ID.reset(request)
    return wrapped

# ==================== 输入校验 ====================

HOST_FORBIDDEN = re.compile(r"[;&|`$(){}\[\]<>'\"]")


def _check_host(host: str) -> Optional[str]:
    """校验主机名/IP，合法返回 None，否则返回错误信息"""
    if not host or not host.strip():
        return "host 不能为空"
    if HOST_FORBIDDEN.search(host):
        return f"host 包含非法字符: {host}"
    return None


def _check_port(port: int) -> Optional[str]:
    """校验端口号，合法返回 None，否则返回错误信息"""
    if not isinstance(port, int) or port < 1 or port > 65535:
        return f"port 超出范围 1-65535: {port}"
    return None


def _check_path(path: str) -> Optional[str]:
    """校验路径，合法返回 None，否则返回错误信息"""
    if not path or not path.strip():
        return "path 不能为空"
    if any(c in path for c in "\r\n\0"):
        return f"path 包含非法字符: {path}"
    return None


# ==================== 辅助函数 ====================


def is_local(host: str) -> bool:
    """判断给定的 host 是否指代本机"""
    return host in ("127.0.0.1", "localhost", "0.0.0.0", "::1")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ok(**kwargs) -> dict:
    """构建成功结果，自动附加时间戳"""
    result = {"ok": True, "timestamp": _now_iso(), "complete": True, "warnings": [],
              "request_id": _REQUEST_ID.get() or str(uuid4())}
    result.update(kwargs)
    return result


def _err(host: str, error: str, **extra) -> dict:
    """构建错误结果，自动附加时间戳"""
    result = {"ok": False, "host": host, "error": error, "timestamp": _now_iso(),
              "complete": False, "warnings": [], "error_code": "COMMAND_FAILED",
              "request_id": _REQUEST_ID.get() or str(uuid4())}
    result.update(extra)
    return result


_LOCAL_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="astromcp-local")
_LOCAL_SEM = threading.BoundedSemaphore(4)


async def _local_call(fn, *args, **kwargs):
    deadline = _TOOL_DEADLINE.get() or time.monotonic()+30
    queue_end = min(deadline, time.monotonic()+2)
    while not _LOCAL_SEM.acquire(blocking=False):
        if time.monotonic() >= queue_end:
            raise TimeoutError("BUSY")
        await asyncio.sleep(.005)
    future = _LOCAL_POOL.submit(fn, *args, **kwargs)
    future.add_done_callback(lambda _: _LOCAL_SEM.release())
    wrapped = asyncio.wrap_future(future)
    wrapped.add_done_callback(lambda f: f.exception() if not f.cancelled() else None)
    return await asyncio.wait_for(asyncio.shield(wrapped), max(deadline-time.monotonic(), .001))


def _percent(value):
    value = float(value)
    if not math.isfinite(value) or not 0 <= value <= 100:
        raise ValueError("invalid utilization")
    return round(value, 1)


def _memory_fields(total, available):
    total, available = int(total), int(available)
    if total <= 0 or not 0 <= available <= total:
        raise ValueError("invalid capacity")
    return {"total_gb": round(total/1024**3, 2), "used_gb": round((total-available)/1024**3, 2),
            "free_gb": round(available/1024**3, 2), "percent": _percent((total-available)/total*100)}


def parse_cpu_linux(text: str) -> float:
    parts = text.strip().splitlines()[-1].split()
    if len(parts) < 15:
        raise ValueError("vmstat fields missing")
    return _percent(100-float(parts[14]))


def parse_cpu_mac(text: str) -> float:
    samples = re.findall(r"([\d.]+)%\s+idle", text)
    if not samples:
        raise ValueError("top idle field missing")
    return _percent(100-float(samples[-1]))


def parse_cpu_windows(text: str) -> float:
    data = json.loads(text)
    items = data if isinstance(data, list) else [data]
    if not items or any(not isinstance(item, dict) or item.get("LoadPercentage") is None for item in items):
        raise ValueError("LoadPercentage missing")
    return _percent(sum(_percent(item["LoadPercentage"]) for item in items)/len(items))


def parse_memory_linux(text: str) -> dict:
    line = next((line for line in text.splitlines() if line.lstrip().startswith("Mem:")), "")
    parts = line.split()
    if len(parts) < 7:
        raise ValueError("free available field missing")
    return _memory_fields(parts[1], parts[6])


def parse_memory_mac(text: str) -> dict:
    lines = text.splitlines()
    total = int(lines[0])
    match = re.search(r"([\d.]+)([GMK]?)\s+unused", text)
    if not match:
        raise ValueError("PhysMem unused field missing")
    free = float(match[1])*{"G":1024**3,"M":1024**2,"K":1024,"":1}[match[2]]
    return {**_memory_fields(total, free), "memory_basis": "macOS PhysMem unused; not Linux MemAvailable"}


def parse_memory_windows(text: str) -> dict:
    data = json.loads(text)
    if not isinstance(data, dict) or any(data.get(key) is None for key in ("TotalVisibleMemorySize", "FreePhysicalMemory")):
        raise ValueError("memory fields missing")
    return _memory_fields(int(data["TotalVisibleMemorySize"])*1024, int(data["FreePhysicalMemory"])*1024)


def parse_disk_unix(text: str) -> dict:
    match = re.fullmatch(r".+?\s+(\d+)\s+(\d+)\s+(\d+)\s+([\d.]+)%\s+.+", text.strip().splitlines()[-1])
    if not match:
        raise ValueError("df fields missing")
    total, used, free = (int(match[i])*1024 for i in (1,2,3))
    if total <= 0 or min(used, free) < 0 or used > total:
        raise ValueError("invalid disk capacity")
    return {"total_gb":round(total/1024**3,2),"used_gb":round(used/1024**3,2),
            "free_gb":round(free/1024**3,2),"percent":_percent(match[4])}


def parse_disk_windows(text: str) -> dict:
    data = json.loads(text)
    if not isinstance(data, dict) or any(data.get(key) is None for key in ("Size", "SizeRemaining")):
        raise ValueError("volume fields missing")
    return _memory_fields(data["Size"], data["SizeRemaining"])


def parse_tcp_output(text: str) -> int:
    if not text.strip():
        raise ValueError("empty netstat output")
    lines = text.splitlines()
    rows = [line for line in lines if line.split() and line.split()[0].lower() in {"tcp", "tcp4", "tcp6"}]
    if not rows and not any(word in text.lower() for word in ("proto", "active", "活动连接")):
        raise ValueError("unrecognized netstat output")
    return len(rows)


async def _collect_remote(metric, os_type, host, port, username, password, key_path, path=None, threshold=85):
    commands = {
        ("cpu","linux"):"LC_ALL=C vmstat 1 2", ("cpu","mac"):"LC_ALL=C top -l 2 -s 1",
        ("memory","linux"):"LC_ALL=C free -b", ("memory","mac"):"LC_ALL=C top -l 1 -s 0",
        ("tcp","linux"):"LC_ALL=C netstat -an", ("tcp","mac"):"LC_ALL=C netstat -an",
        ("tcp","windows"):"netstat -ano",
        ("cpu","windows"):'powershell -NoProfile -Command "Get-CimInstance Win32_Processor | Select-Object LoadPercentage | ConvertTo-Json"',
        ("memory","windows"):'powershell -NoProfile -Command "Get-CimInstance Win32_OperatingSystem | Select-Object TotalVisibleMemorySize,FreePhysicalMemory | ConvertTo-Json"',
    }
    prefix = ""
    if metric == "memory" and os_type == "mac":
        code, prefix, err = await run_remote_command(host,port,username,password,key_path,"sysctl -n hw.memsize")
        if code != 0:
            return _err(host,"Cannot collect macOS capacity",error_code="COMMAND_FAILED")
    command = (build_windows_disk_command(path) if os_type=="windows" else build_unix_disk_command(path)) if metric=="disk" else commands[(metric,os_type)]
    code, out, err = await run_remote_command(host,port,username,password,key_path,command)
    if code != 0:
        known = {"TIMEOUT","BUSY","AUTH_FAILED","HOST_KEY_UNKNOWN","HOST_KEY_CHANGED","UNREACHABLE","OUTPUT_LIMIT","INVALID_INPUT"}
        error_code = err if err in known else "COMMAND_FAILED"
        return _err(host,"Remote collection failed",error_code=error_code)
    try:
        if metric == "cpu":
            parser = {"linux":parse_cpu_linux,"mac":parse_cpu_mac,"windows":parse_cpu_windows}[os_type]
            data = {"percent":parser(out), "sample_window_seconds":1}
        elif metric == "memory":
            parser = {"linux":parse_memory_linux,"mac":parse_memory_mac,"windows":parse_memory_windows}[os_type]
            data = parser(prefix+"\n"+out if prefix else out)
        elif metric == "disk":
            data = (parse_disk_windows if os_type=="windows" else parse_disk_unix)(out)
            data.update(path=path,threshold=threshold,warning=data["percent"]>threshold)
        else:
            data = {"tcp_count":parse_tcp_output(out)}
        return _ok(host=host,**data)
    except (ValueError, TypeError, KeyError, IndexError):
        return _err(host,"Collector output did not match the expected format",error_code="PARSE_ERROR")


async def run_remote_command(host, port, username, password, key_path, command) -> tuple[int, str, str]:
    global _EXECUTOR
    if _EXECUTOR is None:
        _EXECUTOR = SSHExecutor(load_settings(os.environ))
    deadline = _TOOL_DEADLINE.get() or time.monotonic()+load_settings(os.environ).tool_timeout_seconds
    result = await _EXECUTOR.execute(SSHConnection(host, port, username or "", password, key_path), command, deadline=deadline)
    if result.error_code:
        return -1, "", result.error_code
    return result.exit_code, result.stdout, result.stderr


async def detect_remote_os(
    host: str,
    port: int,
    username: Optional[str],
    password: Optional[str],
    key_path: Optional[str],
) -> tuple[Optional[str], str]:
    """
    探测远程主机操作系统类型。
    返回 (os_type, error) — os_type 为 None 表示探测失败。
    """
    if not username:
        return None, "远程查询必须提供 SSH username"

    status, out, err = await run_remote_command(
        host, port, username, password, key_path, "uname"
    )
    if status == 0:
        os_type = "mac" if "Darwin" in out else "linux"
        logger.info("检测到远端 %s 为 %s", host, os_type)
        return os_type, ""

    if status == -1:
        return None, err

    w_status, w_out, w_err = await run_remote_command(
        host, port, username, password, key_path, "cmd.exe /c ver"
    )
    if w_status == 0:
        logger.info("检测到远端 %s 为 windows", host)
        return "windows", ""

    return None, f"无法识别远端系统 (uname: {err or out}, ver: {w_err or w_out})"


# ==================== Tools ====================


@mcp.tool()
@_host_tool
async def check_host_status(
    hostname: Annotated[str, Field(description="主机名或 IP 地址")],
) -> dict:
    """检查目标主机的连通性（通过系统 ping 命令）"""
    err = _check_host(hostname)
    if err:
        return _err(hostname, err)

    param = "-n" if platform.system().lower() == "windows" else "-c"

    try:
        process = await asyncio.create_subprocess_exec(
            "ping", param, "3", hostname,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()

        if process.returncode == 0:
            # 提取关键统计信息，避免泄露原始 IP 等细节
            output = stdout.decode("utf-8", errors="ignore")
            summary = _parse_ping_summary(output)
            return _ok(
                host=hostname,
                reachable=True,
                summary=summary or output.strip(),
            )
        else:
            return _ok(
                host=hostname,
                reachable=False,
                detail=stderr.decode("utf-8", errors="ignore").strip(),
            )
    except FileNotFoundError:
        return _err(hostname, "系统未找到 ping 命令")
    except Exception as e:
        logger.exception("ping %s 异常", hostname)
        return _err(hostname, f"ping 异常: {e}")


def _parse_ping_summary(output: str) -> Optional[str]:
    """从 ping 输出中提取统计摘要行"""
    for keyword in ("平均", "avg", "Average", "rtt", "packet loss", "数据包"):
        for line in output.strip().splitlines():
            if keyword.lower() in line.lower():
                return line.strip()
    return None


@mcp.tool()
@_host_tool
async def check_disk_usage(
    host: Annotated[str, Field(description="目标主机 IP 或域名（本机传 127.0.0.1）)")] = "127.0.0.1",
    path: Annotated[str, Field(description="要检查的磁盘路径（如 / 或 C:）")] = "/",
    threshold: Annotated[
        int, Field(ge=0, le=100, description=f"磁盘使用率告警阈值，默认 {DEFAULT_DISK_THRESHOLD}%")
    ] = DEFAULT_DISK_THRESHOLD,
    username: Annotated[Optional[str], Field(description="SSH 用户名（仅远程需要）")] = None,
    password: Annotated[Optional[str], Field(description="SSH 密码（仅远程需要）")] = None,
    key_path: Annotated[Optional[str], Field(description="SSH 私钥路径（仅远程需要）")] = None,
    port: Annotated[int, Field(description="SSH 端口")] = 22,
) -> dict:
    """检查指定主机的磁盘使用情况（支持本地和跨主机 SSH）"""
    for err in (_check_host(host), _check_path(path), _check_port(port)):
        if err:
            return _err(host, err)

    # ---- 本地 ----
    if is_local(host):
        try:
            usage = await _local_call(psutil.disk_usage, path)
            return _ok(
                host=host,
                path=path,
                total_gb=round(usage.total / (1024**3), 2),
                used_gb=round(usage.used / (1024**3), 2),
                free_gb=round(usage.free / (1024**3), 2),
                percent=usage.percent,
                threshold=threshold,
                warning=usage.percent > threshold,
            )
        except FileNotFoundError:
            return _err(host, f"路径不存在: {path}")
        except PermissionError:
            return _err(host, f"权限不足，无法读取 {path}")
        except Exception as e:
            logger.exception("本地磁盘检查失败")
            return _err(host, f"本地磁盘检查失败: {e}")

    # ---- 远程 ----
    os_type, err_msg = await detect_remote_os(host, port, username, password, key_path)
    if not os_type:
        return _err(host, err_msg)

    if os_type in ("linux", "mac"):
        return await _disk_unix(host, port, username, password, key_path, path, threshold)
    else:
        return await _disk_windows(host, port, username, password, key_path, path, threshold)


async def _disk_unix(host, port, username, password, key_path, path, threshold) -> dict:
    return await _collect_remote("disk","linux",host,port,username,password,key_path,path,threshold)


async def _disk_windows(host, port, username, password, key_path, path, threshold) -> dict:
    return await _collect_remote("disk","windows",host,port,username,password,key_path,path,threshold)


@mcp.tool()
@_host_tool
async def check_mem(
    host: Annotated[str, Field(description="目标主机 IP 或域名")] = "127.0.0.1",
    username: Annotated[Optional[str], Field(description="SSH 用户名")] = None,
    password: Annotated[Optional[str], Field(description="SSH 密码")] = None,
    key_path: Annotated[Optional[str], Field(description="SSH 私钥路径（仅远程需要）")] = None,
    port: Annotated[int, Field(description="SSH 端口")] = 22,
) -> dict:
    """检查指定主机的内存使用情况（支持本地和跨主机 SSH）"""
    for err in (_check_host(host), _check_port(port)):
        if err:
            return _err(host, err)

    if is_local(host):
        try:
            mem = await _local_call(psutil.virtual_memory)
            return _ok(
                host=host,
                total_gb=round(mem.total / (1024**3), 2),
                used_gb=round((mem.total - mem.available) / (1024**3), 2),
                free_gb=round(mem.available / (1024**3), 2),
                percent=mem.percent,
            )
        except Exception as e:
            logger.exception("本地内存检查失败")
            return _err(host, f"本地内存检查失败: {e}")

    os_type, err_msg = await detect_remote_os(host, port, username, password, key_path)
    if not os_type:
        return _err(host, err_msg)

    return await _mem_remote(os_type, host, port, username, password, key_path)


async def _mem_remote(os_type, host, port, username, password, key_path) -> dict:
    """远程内存采集分发"""
    if os_type == "linux":
        return await _mem_linux(host, port, username, password, key_path)
    elif os_type == "mac":
        return await _mem_mac(host, port, username, password, key_path)
    else:
        return await _mem_windows(host, port, username, password, key_path)


async def _mem_linux(host, port, username, password, key_path) -> dict:
    return await _collect_remote("memory","linux",host,port,username,password,key_path)


async def _mem_mac(host, port, username, password, key_path) -> dict:
    return await _collect_remote("memory","mac",host,port,username,password,key_path)


async def _mem_windows(host, port, username, password, key_path) -> dict:
    return await _collect_remote("memory","windows",host,port,username,password,key_path)


@mcp.tool()
@_host_tool
async def check_cpu(
    host: Annotated[str, Field(description="目标主机 IP 或域名")] = "127.0.0.1",
    username: Annotated[Optional[str], Field(description="SSH 用户名")] = None,
    password: Annotated[Optional[str], Field(description="SSH 密码")] = None,
    key_path: Annotated[Optional[str], Field(description="SSH 私钥路径（仅远程需要）")] = None,
    port: Annotated[int, Field(description="SSH 端口")] = 22,
) -> dict:
    """检查指定主机的 CPU 使用率（支持本地和跨主机 SSH）"""
    for err in (_check_host(host), _check_port(port)):
        if err:
            return _err(host, err)

    if is_local(host):
        try:
            return _ok(host=host, percent=await _local_call(psutil.cpu_percent, interval=1), sample_window_seconds=1)
        except Exception as e:
            logger.exception("本地 CPU 检查失败")
            return _err(host, f"本地 CPU 检查失败: {e}")

    os_type, err_msg = await detect_remote_os(host, port, username, password, key_path)
    if not os_type:
        return _err(host, err_msg)

    return await _cpu_remote(os_type, host, port, username, password, key_path)


async def _cpu_remote(os_type, host, port, username, password, key_path) -> dict:
    """远程 CPU 采集分发"""
    if os_type == "linux":
        return await _cpu_linux(host, port, username, password, key_path)
    elif os_type == "mac":
        return await _cpu_mac(host, port, username, password, key_path)
    else:
        return await _cpu_windows(host, port, username, password, key_path)


async def _cpu_linux(host, port, username, password, key_path) -> dict:
    return await _collect_remote("cpu","linux",host,port,username,password,key_path)


async def _cpu_mac(host, port, username, password, key_path) -> dict:
    return await _collect_remote("cpu","mac",host,port,username,password,key_path)


async def _cpu_windows(host, port, username, password, key_path) -> dict:
    return await _collect_remote("cpu","windows",host,port,username,password,key_path)


@mcp.tool()
@_host_tool
async def check_tcp(
    host: Annotated[str, Field(description="目标主机 IP 或域名")] = "127.0.0.1",
    username: Annotated[Optional[str], Field(description="SSH 用户名")] = None,
    password: Annotated[Optional[str], Field(description="SSH 密码")] = None,
    key_path: Annotated[Optional[str], Field(description="SSH 私钥路径（仅远程需要）")] = None,
    port: Annotated[int, Field(description="SSH 端口")] = 22,
) -> dict:
    """检查指定主机的 TCP 连接数（支持本地和跨主机 SSH）"""
    for err in (_check_host(host), _check_port(port)):
        if err:
            return _err(host, err)

    if is_local(host):
        try:
            conns = await _local_call(psutil.net_connections, kind="tcp")
            return _ok(host=host, tcp_count=len(conns))
        except psutil.AccessDenied:
            return _err(host, "本地权限不足，无法获取完整 TCP 连接数")
        except Exception as e:
            logger.exception("本地 TCP 检查失败")
            return _err(host, f"本地 TCP 检查失败: {e}")

    os_type, err_msg = await detect_remote_os(host, port, username, password, key_path)
    if not os_type:
        return _err(host, err_msg)

    return await _tcp_remote(os_type, host, port, username, password, key_path)


async def _tcp_remote(os_type, host, port, username, password, key_path) -> dict:
    return await _collect_remote("tcp",os_type,host,port,username,password,key_path)


@mcp.tool()
@_host_tool
async def check_service_port(
    host: Annotated[str, Field(description="主机名或 IP 地址")],
    port: Annotated[int, Field(description="端口号")],
) -> dict:
    """检查目标主机的指定端口是否可连通（TCP connect）"""
    for err in (_check_host(host), _check_port(port)):
        if err:
            return _err(host, err)

    def _check() -> bool:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(TCP_CHECK_TIMEOUT)
        try:
            result = sock.connect_ex((host, port))
            return result == 0
        except socket.gaierror:
            return False
        except Exception:
            return False
        finally:
            sock.close()

    try:
        is_open = await asyncio.to_thread(_check)
        return _ok(
            host=host,
            port=port,
            open=is_open,
        )
    except Exception as e:
        logger.exception("端口检查异常 %s:%d", host, port)
        return _err(host, f"端口检查异常: {e}")


def main(argv: Sequence[str] | None = None) -> None:
    settings = load_settings(os.environ)
    parser = argparse.ArgumentParser(description="AstroMCP host inspection (trusted local operator)")
    parser.add_argument("--transport", choices=("stdio", "http", "sse"), default=settings.transport)
    parser.add_argument("--host", default=settings.bind_host)
    parser.add_argument("--port", type=int, default=settings.bind_port)
    args = parser.parse_args(argv)
    if args.transport != "stdio":
        if args.host != "localhost" and not ipaddress.ip_address(args.host).is_loopback:
            raise ValueError("Only loopback HTTP/SSE is supported; remote authentication is not configured")
        mcp.run(transport=args.transport, host=args.host, port=args.port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
