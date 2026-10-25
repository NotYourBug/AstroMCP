import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from k8s_check_mcp import client as m


def test_gateway_supplies_timeout_and_closes(legacy_module):
    api = Mock(return_value="ok")
    clients = SimpleNamespace(api_client=Mock())
    gateway = m.K8sGateway(m.load_settings({}), client_factory=lambda: clients)
    assert gateway.call(api, deadline=time.monotonic() + 1) == "ok"
    connect, read = api.call_args.kwargs["_request_timeout"]
    assert 0 < connect <= 3 and 0 < read <= 10
    assert gateway.clients is clients
    gateway.close()
    clients.api_client.close.assert_called_once()


def test_no_request_after_deadline(legacy_module):
    api = Mock()
    gateway = m.K8sGateway(m.load_settings({}))
    with pytest.raises(m.K8sOperationError):
        gateway.call(api, deadline=time.monotonic() - 1)
    api.assert_not_called()
