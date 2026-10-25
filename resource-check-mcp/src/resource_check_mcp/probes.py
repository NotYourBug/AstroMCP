from __future__ import annotations

import asyncio, socket, platform, locale
from typing import Optional
from .models import _ok, _err
from .config import _check_host, _check_port

def _parse_ping_summary(output: str) -> Optional[str]:
    """从 ping 输出中提取统计摘要行"""
    for keyword in ("平均", "avg", "Average", "rtt", "packet loss", "数据包"):
        for line in output.strip().splitlines():
            if keyword.lower() in line.lower():
                return line.strip()
    return None


async def probe_ping(host: str, *, timeout: float = 5.0) -> dict:
    if _check_host(host):
        return _err(host, "Invalid host", error_code="INVALID_INPUT", probe_origin="server")
    process = None
    try:
        flag = "-n" if platform.system().lower() == "windows" else "-c"
        process = await asyncio.create_subprocess_exec("ping", flag, "3", host,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
        encoding = locale.getpreferredencoding(False)
        output = (stdout+stderr).decode(encoding, errors="replace")
        if process.returncode == 0:
            return _ok(host=host, reachable=True, probe_origin="server", summary=_parse_ping_summary(output) or output[:2000])
        if process.returncode == 1:
            return _ok(host=host, reachable=False, probe_origin="server", detail="No successful ICMP reply; this does not prove host failure")
        return _err(host, "ping command failed", error_code="COMMAND_FAILED", probe_origin="server")
    except FileNotFoundError:
        return _err(host, "ping is not installed", error_code="NOT_FOUND", probe_origin="server")
    except (TimeoutError, asyncio.TimeoutError):
        return _err(host, "ping timed out", error_code="TIMEOUT", probe_origin="server")
    finally:
        if process is not None and process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.communicate()
            await process.wait()


async def probe_tcp(host: str, port: int, *, timeout: float = 5.0) -> dict:
    if _check_host(host) or _check_port(port):
        return _err(host, "Invalid host or port", error_code="INVALID_INPUT", open=None, probe_origin="server")
    writer = None
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port,
            family=socket.AF_UNSPEC, happy_eyeballs_delay=.25), timeout)
        return _ok(host=host, port=port, open=True, probe_origin="server")
    except ConnectionRefusedError:
        return _ok(host=host, port=port, open=False, probe_origin="server")
    except socket.gaierror:
        return _err(host, "DNS lookup failed", error_code="DNS_ERROR", open=None, probe_origin="server")
    except (TimeoutError, asyncio.TimeoutError):
        return _err(host, "TCP connection timed out", error_code="TIMEOUT", open=None, probe_origin="server")
    except OSError:
        return _err(host, "TCP target unreachable", error_code="UNREACHABLE", open=None, probe_origin="server")
    finally:
        if writer is not None:
            writer.close()
            await asyncio.wait_for(writer.wait_closed(), min(timeout, 1))

