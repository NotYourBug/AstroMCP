import runpy
from pathlib import Path
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize("kind", ["host", "k8s"])
def test_invalid_cli_port_rejected(kind, monkeypatch):
    if kind == "host":
        import resource_check_mcp.__main__ as main
    else:
        import k8s_check_mcp.__main__ as main
    monkeypatch.setattr(main, "create_server", lambda settings: Mock())
    with pytest.raises(ValueError):
        main.main(["--port", "0"])


@pytest.mark.parametrize("kind", ["host", "k8s"])
def test_network_bind_rejected(kind):
    if kind == "host":
        import resource_check_mcp.__main__ as main
    else:
        import k8s_check_mcp.__main__ as main
    with pytest.raises(ValueError):
        main.main(["--transport", "http", "--host", "0.0.0.0"])


@pytest.mark.parametrize("kind,expected", [("host", "stdio"), ("k8s", "sse")])
def test_legacy_script_defaults(kind, expected, monkeypatch):
    if kind == "host":
        import resource_check_mcp.__main__ as main

        path = "resource-check-mcp/resource-check-mcp.py"
    else:
        import k8s_check_mcp.__main__ as main

        path = "k8s-check-mcp/mcp_server.py"
    called = Mock()
    monkeypatch.setattr(main, "main", called)
    runpy.run_path(str(Path(__file__).resolve().parents[2] / path), run_name="__main__")
    assert called.call_args.kwargs.get("default_transport", "stdio") == expected


def test_k8s_file_transport_is_used(tmp_path, monkeypatch):
    import k8s_check_mcp.__main__ as main

    config = tmp_path / "settings.json"
    config.write_text('{"transport":"http"}')
    monkeypatch.setenv("MCP_CONFIG", str(config))
    monkeypatch.delenv("MCP_TRANSPORT", raising=False)
    run = Mock()
    monkeypatch.setattr(main, "create_server", lambda settings: Mock(run=run))
    main.main([])
    assert run.call_args.kwargs["transport"] == "http"


def test_invalid_host_transport_rejected():
    import pytest
    from resource_check_mcp.config import load_settings

    with pytest.raises(ValueError):
        load_settings({"MCP_TRANSPORT": "invalid"})
