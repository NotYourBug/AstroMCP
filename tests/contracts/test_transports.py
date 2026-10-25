import importlib.util
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('installed_smoke',ROOT/'scripts/smoke_installed.py')


@pytest.mark.timeout(30)
@pytest.mark.parametrize('kind',['host','k8s'])
@pytest.mark.parametrize('transport',['stdio','http','sse'])
async def test_real_transport(kind,transport):
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    await module.smoke(kind,transport)
