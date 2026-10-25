import pytest
from k8s_check_mcp import quantities as m


@pytest.mark.parametrize("text,expected", [("250m", 250), ("0.1m", 1), ("1", 1000)])
def test_cpu_rounds_up(legacy_module, text, expected):
    assert m.cpu_to_millicores(text) == expected


def test_large_memory_precision(legacy_module):
    assert m.memory_to_bytes("9007199254740993") == 9007199254740993
    assert m.memory_to_bytes("1Gi") == 1073741824


@pytest.mark.parametrize("text", ["NaN", "Infinity", "-1", "m", "1wat"])
def test_invalid_quantity_rejected(legacy_module, text):
    with pytest.raises(ValueError):
        m.parse_quantity_decimal(text)
