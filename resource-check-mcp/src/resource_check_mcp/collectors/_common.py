from __future__ import annotations

import math

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

