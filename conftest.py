from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent


def load_module(kind):
    paths = {"host": "resource-check-mcp/resource-check-mcp.py", "k8s": "k8s-check-mcp/mcp_server.py"}
    name = f"baseline_{kind}"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, ROOT / paths[kind])
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


@pytest.fixture
def legacy_module():
    return load_module

