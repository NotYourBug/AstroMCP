"""Factories used by protocol contracts; no legacy runtime loading remains."""

from types import SimpleNamespace

import pytest


def load_module(kind):
    if kind == "host":
        from resource_check_mcp.server import create_server
    elif kind == "k8s":
        from k8s_check_mcp.server import create_server
    else:
        raise ValueError(f"Unknown MCP kind: {kind}")
    return SimpleNamespace(mcp=create_server())


@pytest.fixture
def legacy_module():
    # Fixture name retained for migration tests; it returns the current factory.
    return load_module
