from __future__ import annotations

from ..models import _ok, _err
from .linux import build_unix_disk_command, parse_cpu_linux, parse_memory_linux, parse_disk_unix, parse_tcp_output
from .macos import parse_cpu_mac, parse_memory_mac
from .windows import build_windows_disk_command, parse_cpu_windows, parse_memory_windows, parse_disk_windows

async def _collect_remote(metric, os_type, connection, executor, deadline, path=None, threshold=85):
    host = connection.host
    async def run(command):
        result = await executor.execute(connection, command, deadline=deadline)
        return (-1, "", result.error_code) if result.error_code else (result.exit_code, result.stdout, result.stderr)
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
        code, prefix, err = await run("sysctl -n hw.memsize")
        if code != 0:
            return _err(host,"Cannot collect macOS capacity",error_code="COMMAND_FAILED")
    command = (build_windows_disk_command(path) if os_type=="windows" else build_unix_disk_command(path)) if metric=="disk" else commands[(metric,os_type)]
    code, out, err = await run(command)
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


async def collect_cpu(os_type, connection, executor, deadline):
    return await _collect_remote("cpu", os_type, connection, executor, deadline)

async def collect_memory(os_type, connection, executor, deadline):
    return await _collect_remote("memory", os_type, connection, executor, deadline)

async def collect_disk(os_type, connection, executor, deadline, path, threshold):
    return await _collect_remote("disk", os_type, connection, executor, deadline, path, threshold)

async def collect_tcp(os_type, connection, executor, deadline):
    return await _collect_remote("tcp", os_type, connection, executor, deadline)
