from unittest.mock import Mock
from pathlib import Path
import runpy

import pytest


@pytest.mark.parametrize('kind',['host','k8s'])
def test_invalid_cli_port_rejected(kind,monkeypatch):
    if kind=='host':
        import resource_check_mcp.__main__ as main
    else:
        import k8s_check_mcp.__main__ as main
    monkeypatch.setattr(main,'create_server',lambda settings:Mock())
    with pytest.raises(ValueError):main.main(['--port','0'])


@pytest.mark.parametrize('kind',['host','k8s'])
def test_network_bind_rejected(kind):
    if kind=='host':
        import resource_check_mcp.__main__ as main
    else:
        import k8s_check_mcp.__main__ as main
    with pytest.raises(ValueError):main.main(['--transport','http','--host','0.0.0.0'])


@pytest.mark.parametrize('kind,expected',[('host','stdio'),('k8s','sse')])
def test_legacy_script_defaults(kind,expected,monkeypatch):
    if kind=='host':
        import resource_check_mcp.__main__ as main
        path='resource-check-mcp/resource-check-mcp.py'
    else:
        import k8s_check_mcp.__main__ as main
        path='k8s-check-mcp/mcp_server.py'
    called=Mock()
    monkeypatch.setattr(main,'main',called)
    runpy.run_path(str(Path(__file__).resolve().parents[2]/path),run_name='__main__')
    assert called.call_args.kwargs.get('default_transport','stdio')==expected
