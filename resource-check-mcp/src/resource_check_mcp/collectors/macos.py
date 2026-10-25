from __future__ import annotations

import re
from ._common import _percent, _memory_fields
from .linux import parse_disk_output, parse_tcp_output, build_disk_command

def parse_cpu_mac(text: str) -> float:
    samples = re.findall(r"([\d.]+)%\s+idle", text)
    if not samples:
        raise ValueError("top idle field missing")
    return _percent(100-float(samples[-1]))


def parse_memory_mac(text: str) -> dict:
    lines = text.splitlines()
    total = int(lines[0])
    match = re.search(r"([\d.]+)([GMK]?)\s+unused", text)
    if not match:
        raise ValueError("PhysMem unused field missing")
    free = float(match[1])*{"G":1024**3,"M":1024**2,"K":1024,"":1}[match[2]]
    return {**_memory_fields(total, free), "memory_basis": "macOS PhysMem unused; not Linux MemAvailable"}


parse_cpu_output = parse_cpu_mac
parse_memory_output = parse_memory_mac
