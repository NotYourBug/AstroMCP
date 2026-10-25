import pytest


def test_k8s_non_loopback_rejected(legacy_module):
    with pytest.raises(ValueError):legacy_module("k8s").main(['--transport','http','--host','0.0.0.0'])
