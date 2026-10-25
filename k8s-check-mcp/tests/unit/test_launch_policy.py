from k8s_check_mcp import __main__ as m
import pytest


def test_k8s_non_loopback_rejected(legacy_module):
    with pytest.raises(ValueError):m.main(['--transport','http','--host','0.0.0.0'])
