from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent


def load_module(kind):
    if kind == "k8s":
        from types import SimpleNamespace
        from k8s_check_mcp.server import create_server
        return SimpleNamespace(mcp=create_server())
    if kind == "host":
        from types import SimpleNamespace
        from resource_check_mcp.server import create_server
        return SimpleNamespace(mcp=create_server())
    paths = {"host": "resource-check-mcp/resource-check-mcp.py", "k8s": "k8s-check-mcp/mcp_server.py"}
    name = f"baseline_{kind}"
    if name not in sys.modules:
        source = os.environ.get("ASTROMCP_HOST_SOURCE") if kind == "host" else None
        spec = importlib.util.spec_from_file_location(name, source or ROOT / paths[kind])
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


@pytest.fixture
def legacy_module():
    return load_module

