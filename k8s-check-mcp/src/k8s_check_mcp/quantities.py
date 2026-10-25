from __future__ import annotations

from decimal import ROUND_CEILING, Decimal, InvalidOperation

from kubernetes.utils.quantity import parse_quantity as sdk_parse_quantity


def parse_quantity_decimal(text: str) -> Decimal:
    text = str(text).strip()
    if not text or len(text) > 128:
        raise ValueError("Invalid resource quantity")
    try:
        value = sdk_parse_quantity(text)
    except (ValueError, InvalidOperation, OverflowError) as exc:
        raise ValueError("Invalid resource quantity") from exc
    if not value.is_finite() or value < 0 or value > Decimal(2**63 - 1):
        raise ValueError("Resource quantity is outside the supported range")
    return value


def cpu_to_millicores(text: str) -> int:
    return int((parse_quantity_decimal(text) * 1000).to_integral_value(rounding=ROUND_CEILING))


def memory_to_bytes(text: str) -> int:
    return int(parse_quantity_decimal(text).to_integral_value(rounding=ROUND_CEILING))


def parse_quantity(q: str) -> float:
    return float(parse_quantity_decimal(q))


def _cpu_to_millicores(quantity: str) -> int:
    return cpu_to_millicores(quantity)


def _memory_to_bytes(quantity: str) -> int:
    return memory_to_bytes(quantity)
