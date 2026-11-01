import asyncio
import socket
from types import SimpleNamespace as N
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


@pytest.mark.parametrize("close_error", [False, True])
async def test_tcp_close_reset_preserves_open(monkeypatch, close_error):
    from resource_check_mcp.probes import probe_tcp

    writer = Mock(wait_closed=AsyncMock())
    if close_error:
        writer.close.side_effect = OSError("close failed")
    else:
        writer.wait_closed.side_effect = ConnectionResetError("reset")
    monkeypatch.setattr(asyncio, "open_connection", AsyncMock(return_value=(None, writer)))
    result = await probe_tcp("localhost", 80)
    assert result["ok"] and result["open"] is True and not result["complete"]
    assert "TCP_CLEANUP_FAILED" in result["warnings"]


async def test_tcp_close_timeout_preserves_open(monkeypatch):
    from resource_check_mcp.probes import probe_tcp

    writer = Mock(wait_closed=AsyncMock(side_effect=TimeoutError()))
    monkeypatch.setattr(asyncio, "open_connection", AsyncMock(return_value=(None, writer)))
    result = await probe_tcp("localhost", 80)
    assert result["ok"] and result["open"] is True and not result["complete"]
    assert "TCP_CLEANUP_TIMEOUT" in result["warnings"]


async def test_tcp_cleanup_uses_remaining_budget(monkeypatch):
    import resource_check_mcp.probes as probes
    from resource_check_mcp.models import _TOOL_DEADLINE

    clock = [100.0]
    monkeypatch.setattr(probes, "time", N(monotonic=lambda: clock[0]), raising=False)
    writer = Mock(wait_closed=AsyncMock())

    async def connect(*args, **kwargs):
        clock[0] = 100.04
        return None, writer

    monkeypatch.setattr(asyncio, "open_connection", connect)
    original_wait_for = asyncio.wait_for
    budgets = []

    async def observe(awaitable, timeout):
        budgets.append(timeout)
        return await original_wait_for(awaitable, timeout)

    monkeypatch.setattr(asyncio, "wait_for", observe)
    token = _TOOL_DEADLINE.set(100.05)
    try:
        result = await probes.probe_tcp("localhost", 80, timeout=0.05)
        assert result["ok"]
        assert budgets == pytest.approx([0.05, 0.01])
    finally:
        _TOOL_DEADLINE.reset(token)


async def test_tcp_cleanup_skips_wait_without_budget(monkeypatch):
    import resource_check_mcp.probes as probes

    clock = [100.0]
    monkeypatch.setattr(probes, "time", N(monotonic=lambda: clock[0]), raising=False)
    writer = Mock(wait_closed=AsyncMock())

    async def connect(*args, **kwargs):
        clock[0] = 101.0
        return None, writer

    monkeypatch.setattr(asyncio, "open_connection", connect)
    result = await probes.probe_tcp("localhost", 80, timeout=0.05)
    assert result["open"] is True and not result["complete"]
    assert "TCP_CLEANUP_TIMEOUT" in result["warnings"]
    writer.wait_closed.assert_not_called()


async def test_tcp_cancel_during_cleanup_propagates(monkeypatch):
    from resource_check_mcp.probes import probe_tcp

    started = asyncio.Event()

    async def wait_closed():
        started.set()
        await asyncio.Event().wait()

    writer = Mock(wait_closed=AsyncMock(side_effect=wait_closed))
    monkeypatch.setattr(asyncio, "open_connection", AsyncMock(return_value=(None, writer)))
    task = asyncio.create_task(probe_tcp("localhost", 80))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    writer.close.assert_called_once()
