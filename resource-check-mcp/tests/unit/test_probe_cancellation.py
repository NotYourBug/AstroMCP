import asyncio
from unittest.mock import AsyncMock


async def test_ping_timeout_reaps_child(legacy_module, monkeypatch):
    import resource_check_mcp.probes as m

    class Process:
        returncode = None
        killed = False
        waited = False

        async def communicate(self):
            if not self.killed:
                await asyncio.sleep(1)
            return b"", b""

        def kill(self):
            self.killed = True
            self.returncode = -9

        async def wait(self):
            self.waited = True

    process = Process()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    result = await m.probe_ping("localhost", timeout=0.01)
    assert result["error_code"] == "TIMEOUT"
    assert process.killed and process.waited
