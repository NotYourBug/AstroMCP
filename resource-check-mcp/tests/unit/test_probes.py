import asyncio
import socket
from unittest.mock import AsyncMock, Mock

import pytest
import resource_check_mcp.config as host_config


@pytest.mark.parametrize(
    "error,code,open_value",
    [
        (ConnectionRefusedError(), None, False),
        (socket.gaierror(), "DNS_ERROR", None),
        (TimeoutError(), "TIMEOUT", None),
    ],
)
async def test_tcp_failure_types(legacy_module, monkeypatch, error, code, open_value):
    import resource_check_mcp.probes as m

    monkeypatch.setattr(asyncio, "open_connection", AsyncMock(side_effect=error))
    result = await m.probe_tcp("example.test", 80, timeout=0.05)
    assert result["open"] is open_value
    assert result.get("error_code") == code
    assert result["probe_origin"] == "server"


async def test_ipv6_probe(legacy_module, monkeypatch):
    import resource_check_mcp.probes as m

    writer = Mock(wait_closed=AsyncMock())
    connect = AsyncMock(return_value=(None, writer))
    monkeypatch.setattr(asyncio, "open_connection", connect)
    result = await m.probe_tcp("::1", 80)
    assert result["open"] is True
    assert connect.call_args.kwargs["family"] == socket.AF_UNSPEC
    writer.close.assert_called_once()


@pytest.mark.parametrize("host", ["-n", "host\nwhoami", "host name"])
def test_host_options_rejected(legacy_module, host):
    assert host_config._check_host(host) is not None
