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
import os
import platform
import re
import shlex
import socket
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
    result = {"ok": True, "timestamp": _now_iso()}
    result.update(kwargs)
    return result


def _err(host: str, error: str, **extra) -> dict:
    """构建错误结果，自动附加时间戳"""
    result = {"ok": False, "host": host, "error": error, "timestamp": _now_iso()}
    result.update(extra)
    return result


async def run_remote_command(
    host: str,
    port: int,
    username: Optional[str],
    password: Optional[str],
    key_path: Optional[str],
    command: str,
) -> tuple[int, str, str]:
    """
    通过 SSH 在远程主机执行命令。
    返回 (exit_status, stdout, stderr) — exit_status 为 -1 表示连接层错误。
    """

    def _run() -> tuple[int, str, str]:
        if not username:
            return -1, "", "远程执行需要提供 SSH username"

        client = create_ssh_client(load_settings(os.environ))

        try:
            client.connect(
                hostname=host,
                port=port,
                username=username,
                password=password,
                key_filename=key_path,
                allow_agent=True,
                look_for_keys=True,
                timeout=SSH_CONNECT_TIMEOUT,
                banner_timeout=SSH_CONNECT_TIMEOUT,
                auth_timeout=SSH_CONNECT_TIMEOUT,
            )

            _, stdout, stderr = client.exec_command(command, timeout=SSH_COMMAND_TIMEOUT)
            exit_status = stdout.channel.recv_exit_status()
            out = stdout.read().decode("utf-8", errors="ignore").strip()
            err = stderr.read().decode("utf-8", errors="ignore").strip()

            logger.info("ssh %s@%s:%d cmd=%r → exit=%d", username, host, port, command, exit_status)
            if exit_status != 0:
                logger.warning("ssh cmd failed: %s", err or out)

            return exit_status, out, err

        except paramiko.AuthenticationException as e:
            logger.error("ssh 认证失败 %s@%s:%d — %s", username, host, port, e)
            return -1, "", f"认证失败: {e}"
        except (paramiko.SSHException, socket.error) as e:
            logger.error("ssh 连接异常 %s@%s:%d — %s", username, host, port, e)
            return -1, "", f"SSH 连接异常: {e}"
        except Exception as e:
            logger.error("ssh 未知错误 %s@%s:%d — %s", username, host, port, e)
            return -1, "", str(e)
        finally:
            client.close()

    return await asyncio.to_thread(_run)


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

    w_status, w_out, w_err = await run_remote_command(
        host, port, username, password, key_path, "cmd.exe /c ver"
    )
    if w_status == 0:
        logger.info("检测到远端 %s 为 windows", host)
        return "windows", ""

    return None, f"无法识别远端系统 (uname: {err or out}, ver: {w_err or w_out})"


# ==================== Tools ====================


@mcp.tool()
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
            usage = psutil.disk_usage(path)
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
    """Unix (Linux/macOS) 磁盘检查 — 使用 df -kP"""
    status, out, err = await run_remote_command(
        host, port, username, password, key_path, build_unix_disk_command(path)
    )
    if status != 0:
        return _err(host, "执行 df 命令失败", detail=err or out)

    lines = out.strip().split("\n")
    if len(lines) < 2:
        return _err(host, "df 输出异常", raw=out)

    # df -kP 格式: Filesystem 1024-blocks Used Available Capacity Mounted on
    # 挂载点可能含空格，所以用从右向左的解析策略
    parts = lines[-1].split()
    if len(parts) < 5:
        return _err(host, "解析 df 输出失败 — 字段不足", raw=out)

    try:
        total_kb = int(parts[1])
        used_kb = int(parts[2])
        free_kb = int(parts[3])
        pct = float(parts[4].rstrip("%"))
        return _ok(
            host=host,
            path=path,
            total_gb=round(total_kb / (1024**2), 2),
            used_gb=round(used_kb / (1024**2), 2),
            free_gb=round(free_kb / (1024**2), 2),
            percent=pct,
            threshold=threshold,
            warning=pct > threshold,
        )
    except (ValueError, IndexError) as e:
        return _err(host, f"解析 df 输出失败: {e}", raw=out)


async def _disk_windows(host, port, username, password, key_path, path, threshold) -> dict:
    """Windows 磁盘检查 — 使用 PowerShell Get-Volume"""
    cmd = build_windows_disk_command(path)
    drive = path[0].upper()
    status, out, err = await run_remote_command(host, port, username, password, key_path, cmd)
    if status != 0:
        return _err(host, "执行 PowerShell Get-Volume 失败", detail=err or out)

    try:
        data = json.loads(out)
        total = data.get("Size", 0) or 0
        free = data.get("SizeRemaining", 0) or 0
        used = total - free
        pct = round((used / total) * 100, 1) if total > 0 else 0
        return _ok(
            host=host,
            path=f"{drive}:",
            total_gb=round(total / (1024**3), 2),
            used_gb=round(used / (1024**3), 2),
            free_gb=round(free / (1024**3), 2),
            percent=pct,
            threshold=threshold,
            warning=pct > threshold,
        )
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        return _err(host, f"解析 PowerShell 输出失败: {e}", raw=out)


@mcp.tool()
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
            mem = psutil.virtual_memory()
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
    """Linux 内存 — free -b"""
    status, out, err = await run_remote_command(
        host, port, username, password, key_path, "free -b"
    )
    if status != 0:
        return _err(host, "执行 free 命令失败", detail=err or out)

    lines = out.strip().split("\n")
    if len(lines) < 2:
        return _err(host, "free 输出异常", raw=out)

    parts = lines[1].split()
    try:
        total = int(parts[1])
        used = int(parts[2])
        available = int(parts[6]) if len(parts) > 6 else (total - used)
        pct = round((total - available) / total * 100, 1) if total > 0 else 0
        return _ok(
            host=host,
            total_gb=round(total / (1024**3), 2),
            used_gb=round((total - available) / (1024**3), 2),
            free_gb=round(available / (1024**3), 2),
            percent=pct,
        )
    except (ValueError, IndexError, ZeroDivisionError) as e:
        return _err(host, f"解析 free 输出失败: {e}", raw=out)


async def _mem_mac(host, port, username, password, key_path) -> dict:
    """macOS 内存 — sysctl + top"""
    # 获取总内存
    status, out, err = await run_remote_command(
        host, port, username, password, key_path, "sysctl -n hw.memsize"
    )
    if status != 0:
        return _err(host, "获取 macOS 总内存失败", detail=err or out)

    try:
        total = int(out.strip())
    except ValueError:
        return _err(host, "解析 sysctl hw.memsize 失败", raw=out)

    # 获取使用情况 — 用 top 的 PhysMem 行
    status, out, err = await run_remote_command(
        host, port, username, password, key_path, "top -l 1 -s 0 | grep PhysMem"
    )
    if status != 0 or not out:
        # 退化：只返回总内存
        return _ok(
            host=host,
            total_gb=round(total / (1024**3), 2),
            used_gb=None,
            free_gb=None,
            percent=None,
            note="macOS 实时内存详情获取失败，仅返回总容量",
        )

    # PhysMem 格式: "PhysMem: 16G used (2.4G wired), 16G unused."
    m_used = re.search(r"PhysMem:\s*(\S+)\s+used", out)
    m_unused = re.search(r"(\S+)\s+unused", out)

    def _parse_mem_str(s: str) -> int:
        """将 '16G' / '512M' / '1.5G' 转换为字节"""
        s = s.strip().upper()
        if s.endswith("G"):
            return int(float(s[:-1]) * (1024**3))
        elif s.endswith("M"):
            return int(float(s[:-1]) * (1024**2))
        elif s.endswith("K"):
            return int(float(s[:-1]) * 1024)
        else:
            return int(float(s))

    if m_used and m_unused:
        try:
            used = _parse_mem_str(m_used.group(1))
            free = _parse_mem_str(m_unused.group(1))
            pct = round((used / total) * 100, 1) if total > 0 else 0
            return _ok(
                host=host,
                total_gb=round(total / (1024**3), 2),
                used_gb=round(used / (1024**3), 2),
                free_gb=round(free / (1024**3), 2),
                percent=pct,
            )
        except ValueError as e:
            return _err(host, f"解析 macOS PhysMem 失败: {e}", raw=out)

    return _err(host, "解析 macOS PhysMem 失败 — 无法匹配格式", raw=out)


async def _mem_windows(host, port, username, password, key_path) -> dict:
    """Windows 内存 — Get-CimInstance（wmic 的现代替代）"""
    cmd = (
        'powershell -NoProfile -Command "'
        "Get-CimInstance Win32_OperatingSystem | "
        "Select-Object TotalVisibleMemorySize, FreePhysicalMemory | ConvertTo-Json\""
    )
    status, out, err = await run_remote_command(host, port, username, password, key_path, cmd)
    if status != 0:
        return _err(host, "执行 Get-CimInstance 失败", detail=err or out)

    try:
        data = json.loads(out)
        # WMI 返回的值为 KB
        total = (data.get("TotalVisibleMemorySize", 0) or 0) * 1024
        free = (data.get("FreePhysicalMemory", 0) or 0) * 1024
        used = total - free
        pct = round((used / total) * 100, 1) if total > 0 else 0
        return _ok(
            host=host,
            total_gb=round(total / (1024**3), 2),
            used_gb=round(used / (1024**3), 2),
            free_gb=round(free / (1024**3), 2),
            percent=pct,
        )
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        return _err(host, f"解析 Windows 内存信息失败: {e}", raw=out)


@mcp.tool()
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
            return _ok(host=host, percent=psutil.cpu_percent(interval=1))
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
    """Linux CPU — vmstat"""
    cmd = "vmstat 1 2 | tail -1 | awk '{print 100 - $15}'"
    status, out, err = await run_remote_command(host, port, username, password, key_path, cmd)
    if status != 0:
        return _err(host, "获取 Linux CPU 使用率失败", detail=err or out)
    try:
        return _ok(host=host, percent=float(out.strip()))
    except ValueError:
        return _err(host, "解析 vmstat 输出失败", raw=out)


async def _cpu_mac(host, port, username, password, key_path) -> dict:
    """macOS CPU — top"""
    cmd = "top -l 1 | awk '/^CPU usage/ {print $3}' | sed 's/%//'"
    status, out, err = await run_remote_command(host, port, username, password, key_path, cmd)
    if status != 0:
        return _err(host, "获取 macOS CPU 使用率失败", detail=err or out)
    try:
        return _ok(host=host, percent=float(out.strip()))
    except ValueError:
        return _err(host, "解析 top 输出失败", raw=out)


async def _cpu_windows(host, port, username, password, key_path) -> dict:
    """Windows CPU — Get-CimInstance（wmic 的现代替代）"""
    cmd = (
        'powershell -NoProfile -Command "'
        "Get-CimInstance Win32_Processor | "
        "Select-Object LoadPercentage | ConvertTo-Json\""
    )
    status, out, err = await run_remote_command(host, port, username, password, key_path, cmd)
    if status != 0:
        return _err(host, "获取 Windows CPU 使用率失败", detail=err or out)

    try:
        data = json.loads(out)
        # Get-CimInstance 可能返回单个对象或数组
        if isinstance(data, list):
            percentages = [item.get("LoadPercentage", 0) or 0 for item in data]
            avg_pct = sum(percentages) / len(percentages) if percentages else 0
        else:
            avg_pct = data.get("LoadPercentage", 0) or 0
        return _ok(host=host, percent=round(float(avg_pct), 1))
    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
        return _err(host, f"解析 Windows CPU 信息失败: {e}", raw=out)


@mcp.tool()
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
            conns = [c for c in psutil.net_connections(kind="tcp")]
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
    """远程 TCP 连接数采集分发"""
    if os_type in ("linux", "mac"):
        cmd = "netstat -an | grep -i tcp | wc -l"
    else:
        cmd = 'netstat -ano | find /c "TCP"'

    status, out, err = await run_remote_command(
        host, port, username, password, key_path, cmd
    )
    if status != 0:
        return _err(host, "获取 TCP 连接数失败", detail=err or out)

    try:
        return _ok(host=host, tcp_count=int(out.strip()))
    except ValueError:
        return _err(host, "解析 TCP 连接数失败", raw=out)


@mcp.tool()
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
