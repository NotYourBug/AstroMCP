import time

from resource_check_mcp.config import HostSettings
from resource_check_mcp.models import SSHConnection
from resource_check_mcp.ssh import SSHExecutor
from test_ssh_executor import Channel, SSHClient


async def test_probes_receive_remaining_tool_budget(monkeypatch):
    import resource_check_mcp.server as module

    observed = []

    async def probe(*args, timeout=5):
        observed.append(timeout)
        return {"ok": True}

    monkeypatch.setattr(module, "probe_ping", probe)
    monkeypatch.setattr(module, "probe_tcp", probe)
    service = module.HostService(HostSettings(tool_timeout_seconds=0.05, queue_timeout_seconds=0))
    try:
        await service.check_host_status("localhost")
        await service.check_service_port("localhost", 80)
        assert len(observed) == 2 and all(0 < value <= 0.05 for value in observed)
    finally:
        await service.aclose()


async def test_zero_queue_budget_allows_idle_executor():
    executor = SSHExecutor(
        HostSettings(queue_timeout_seconds=0), client_factory=lambda: SSHClient(Channel())
    )
    try:
        result = await executor.execute(
            SSHConnection("test", 22, "user"), "test", deadline=time.monotonic() + 1
        )
        assert result.exit_code == 0 and result.error_code is None
    finally:
        await executor.aclose()


async def test_elapsed_tool_deadline_is_timeout_not_busy():
    created = []
    executor = SSHExecutor(HostSettings(), client_factory=lambda: created.append(True))
    try:
        result = await executor.execute(
            SSHConnection("test", 22, "user"), "test", deadline=time.monotonic() - 1
        )
        assert result.error_code == "TIMEOUT"
        assert not created
    finally:
        await executor.aclose()
