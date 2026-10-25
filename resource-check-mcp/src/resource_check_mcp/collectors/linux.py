from __future__ import annotations

import re
import shlex

from ._common import _memory_fields, _percent


def build_unix_disk_command(path: str) -> str:
    if not path.startswith("/") or any(c in path for c in "\r\n\0"):
        raise ValueError("Unix disk path must be absolute without control characters")
    return f"df -kP {shlex.quote(path)}"


def parse_cpu_linux(text: str) -> float:
    parts = text.strip().splitlines()[-1].split()
    if len(parts) < 15:
        raise ValueError("vmstat fields missing")
    return _percent(100 - float(parts[14]))


def parse_memory_linux(text: str) -> dict:
    line = next((line for line in text.splitlines() if line.lstrip().startswith("Mem:")), "")
    parts = line.split()
    if len(parts) < 7:
        raise ValueError("free available field missing")
    return _memory_fields(parts[1], parts[6])


def parse_disk_unix(text: str) -> dict:
    match = re.fullmatch(
        r".+?\s+(\d+)\s+(\d+)\s+(\d+)\s+([\d.]+)%\s+.+", text.strip().splitlines()[-1]
    )
    if not match:
        raise ValueError("df fields missing")
    total, used, free = (int(match[i]) * 1024 for i in (1, 2, 3))
    if total <= 0 or min(used, free) < 0 or used > total:
        raise ValueError("invalid disk capacity")
    return {
        "total_gb": round(total / 1024**3, 2),
        "used_gb": round(used / 1024**3, 2),
        "free_gb": round(free / 1024**3, 2),
        "percent": _percent(match[4]),
    }


def parse_tcp_output(text: str) -> int:
    if not text.strip():
        raise ValueError("empty netstat output")
    lines = text.splitlines()
    rows = [
        line
        for line in lines
        if line.split() and line.split()[0].lower() in {"tcp", "tcp4", "tcp6"}
    ]
    if not rows and not any(word in text.lower() for word in ("proto", "active", "活动连接")):
        raise ValueError("unrecognized netstat output")
    return len(rows)


build_disk_command = build_unix_disk_command
parse_cpu_output = parse_cpu_linux
parse_memory_output = parse_memory_linux
parse_disk_output = parse_disk_unix
