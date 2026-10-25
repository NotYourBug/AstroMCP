import asyncio
import threading


async def test_cpu_sampling_does_not_block_loop(legacy_module, monkeypatch):
    import resource_check_mcp.collectors.local as m
    from resource_check_mcp.config import load_settings

    collector = m.LocalCollector(load_settings({}))
    started, release = threading.Event(), threading.Event()

    def cpu_percent(interval):
        started.set()
        release.wait(0.5)
        return 25.0

    monkeypatch.setattr(m.psutil, "cpu_percent", cpu_percent)
    task = asyncio.create_task(collector.collect_cpu())
    while not started.is_set():
        await asyncio.sleep(0.001)
    assert not task.done()
    release.set()
    assert (await task)["percent"] == 25
    await collector.aclose()
