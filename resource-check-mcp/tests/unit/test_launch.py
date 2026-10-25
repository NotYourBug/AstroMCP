import resource_check_mcp.config as host_config
import resource_check_mcp.__main__ as host_main
from unittest.mock import Mock

import pytest


def test_stdio_entrypoint(legacy_module, monkeypatch):
    import resource_check_mcp.__main__ as module
    run = Mock()
    monkeypatch.setattr(module, "create_server", lambda settings: Mock(run=run))
    module.main(["--transport", "stdio"])
    assert run.call_args.kwargs["transport"] == "stdio"


def test_remote_bind_rejected(legacy_module):
    with pytest.raises(ValueError):
        host_main.main(["--transport", "http", "--host", "0.0.0.0"])


def test_threshold_validation(legacy_module):
    with pytest.raises(ValueError):
        host_config.load_settings({"MCP_DISK_THRESHOLD": "101"})

