from __future__ import annotations

import json, re
from ._common import _percent, _memory_fields
from .linux import parse_tcp_output

def build_windows_disk_command(path: str) -> str:
    if not re.fullmatch(r"[A-Za-z]:(?:[\\/][^\r\n\0]*)?", path):
        raise ValueError("Windows disk path must have an explicit drive letter")
    drive = path[0].upper()
    return ('powershell -NoProfile -Command "'
            f'Get-Volume -DriveLetter {drive} | Select-Object Size, SizeRemaining | ConvertTo-Json"')


def parse_cpu_windows(text: str) -> float:
    data = json.loads(text)
    items = data if isinstance(data, list) else [data]
    if not items or any(not isinstance(item, dict) or item.get("LoadPercentage") is None for item in items):
        raise ValueError("LoadPercentage missing")
    return _percent(sum(_percent(item["LoadPercentage"]) for item in items)/len(items))


def parse_memory_windows(text: str) -> dict:
    data = json.loads(text)
    if not isinstance(data, dict) or any(data.get(key) is None for key in ("TotalVisibleMemorySize", "FreePhysicalMemory")):
        raise ValueError("memory fields missing")
    return _memory_fields(int(data["TotalVisibleMemorySize"])*1024, int(data["FreePhysicalMemory"])*1024)


def parse_disk_windows(text: str) -> dict:
    data = json.loads(text)
    if not isinstance(data, dict) or any(data.get(key) is None for key in ("Size", "SizeRemaining")):
        raise ValueError("volume fields missing")
    return _memory_fields(data["Size"], data["SizeRemaining"])


build_disk_command = build_windows_disk_command
parse_cpu_output = parse_cpu_windows
parse_memory_output = parse_memory_windows
parse_disk_output = parse_disk_windows
