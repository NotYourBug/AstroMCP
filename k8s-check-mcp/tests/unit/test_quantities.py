import pytest


@pytest.mark.parametrize('text,expected',[('250m',250),('0.1m',1),('1',1000)])
def test_cpu_rounds_up(legacy_module,text,expected):
    assert legacy_module('k8s').cpu_to_millicores(text)==expected


def test_large_memory_precision(legacy_module):
    m=legacy_module('k8s')
    assert m.memory_to_bytes('9007199254740993')==9007199254740993
    assert m.memory_to_bytes('1Gi')==1073741824


@pytest.mark.parametrize('text',['NaN','Infinity','-1','m','1wat'])
def test_invalid_quantity_rejected(legacy_module,text):
    with pytest.raises(ValueError):legacy_module('k8s').parse_quantity_decimal(text)
