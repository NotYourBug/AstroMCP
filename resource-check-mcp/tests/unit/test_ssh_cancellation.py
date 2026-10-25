import asyncio
import threading
import time

from resource_check_mcp.config import load_settings
from test_ssh_executor import Channel, SSHClient


async def test_cancel_retains_permit_until_worker_exits(legacy_module):
    import resource_check_mcp.ssh as m

    started, release = threading.Event(), threading.Event()

    class SlowClient(SSHClient):
        def connect(self, **kwargs):
            started.set()
            release.wait(2)

    executor = m.SSHExecutor(
        load_settings({"MCP_SSH_MAX_IN_FLIGHT": "1", "MCP_QUEUE_TIMEOUT_SECONDS": "0.02"}),
        client_factory=lambda: SlowClient(Channel()),
    )
    connection = m.SSHConnection("test", 22, "user", None, None)
    task = asyncio.create_task(executor.execute(connection, "uname", deadline=time.monotonic() + 2))
    while not started.is_set():
        await asyncio.sleep(0.001)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    result = await executor.execute(connection, "uname", deadline=time.monotonic() + 0.1)
    assert result.error_code == "BUSY"
    release.set()
    await executor.aclose()
