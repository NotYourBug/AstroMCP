from unittest.mock import Mock

import pytest


def test_stdio_entrypoint(legacy_module, monkeypatch):
    module = legacy_module("host")
    run = Mock()
    monkeypatch.setattr(module.mcp, "run", run)
    module.main(["--transport", "stdio"])
    assert run.call_args.kwargs["transport"] == "stdio"


def test_remote_bind_rejected(legacy_module):
    with pytest.raises(ValueError):
        legacy_module("host").main(["--transport", "http", "--host", "0.0.0.0"])


def test_threshold_validation(legacy_module):
    with pytest.raises(ValueError):
        legacy_module("host").load_settings({"MCP_DISK_THRESHOLD": "101"})

