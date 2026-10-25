import asyncio
import threading


async def test_cpu_sampling_does_not_block_loop(legacy_module, monkeypatch):
    m=legacy_module("host")
    started, release=threading.Event(),threading.Event()
    def cpu_percent(interval):
        started.set()
        release.wait(.5)
        return 25.0
    monkeypatch.setattr(m.psutil,"cpu_percent",cpu_percent)
    task=asyncio.create_task(m.check_cpu())
    while not started.is_set(): await asyncio.sleep(.001)
    assert not task.done()
    release.set()
    assert (await task)["percent"]==25

