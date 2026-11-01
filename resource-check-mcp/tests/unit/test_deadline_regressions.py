import asyncio
import threading
from types import SimpleNamespace as N
from unittest.mock import Mock

import pytest
from resource_check_mcp.collectors import local
from resource_check_mcp.config import HostSettings
from resource_check_mcp.models import _TOOL_DEADLINE, HostOperationError
from resource_check_mcp.server import HostService


async def test_expired_deadline_does_not_submit_local_work(monkeypatch):
    monkeypatch.setattr(local, "time", N(monotonic=lambda: 100.0))
    collector = local.LocalCollector(HostSettings())
    token = _TOOL_DEADLINE.set(99.0)
    worker = Mock(return_value=42)
    try:
        with pytest.raises(HostOperationError) as error:
            await collector.call(worker)
        assert error.value.code == "TIMEOUT"
        worker.assert_not_called()
    finally:
        _TOOL_DEADLINE.reset(token)
        await collector.aclose()


@pytest.mark.parametrize("after,code", [(102.0, "TIMEOUT"), (100.5, "BUSY")])
async def test_elapsed_deadline_beats_busy(monkeypatch, after, code):
    clock = [100.0]
    monkeypatch.setattr(local, "time", N(monotonic=lambda: clock[0]))

    async def advance(_):
        clock[0] = after

    monkeypatch.setattr(local, "asyncio", N(sleep=advance))
    collector = local.LocalCollector(HostSettings(queue_timeout_seconds=0.1))
    for _ in range(4):
        assert collector._sem.acquire(blocking=False)
    token = _TOOL_DEADLINE.set(101.0)
    try:
        with pytest.raises(HostOperationError) as error:
            await collector.call(Mock())
        assert error.value.code == code
    finally:
        _TOOL_DEADLINE.reset(token)
        for _ in range(4):
            collector._sem.release()
        await collector.aclose()


async def test_completed_local_result_after_deadline_is_timeout(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(local, "time", N(monotonic=lambda: clock[0]))
    collector = local.LocalCollector(HostSettings())
    token = _TOOL_DEADLINE.set(101.0)

    def worker():
        clock[0] = 102.0
        return 42

    try:
        with pytest.raises(HostOperationError) as error:
            await collector.call(worker)
        assert error.value.code == "TIMEOUT"
    finally:
        _TOOL_DEADLINE.reset(token)
        await collector.aclose()


async def test_tool_publication_rechecks_deadline(monkeypatch):
    import resource_check_mcp.server as server

    clock = [100.0]
    monkeypatch.setattr(server, "time", N(monotonic=lambda: clock[0]))
    service = HostService(HostSettings())

    async def completed():
        clock[0] = 131.0
        return {"total_gb": 1}

    monkeypatch.setattr(service.local, "collect_memory", completed)
    try:
        result = await service.check_mem()
        assert not result["ok"] and not result["complete"]
        assert result["error_code"] == "TIMEOUT"
    finally:
        await service.aclose()


@pytest.mark.parametrize("cancel", [False, True])
async def test_local_cancel_and_timeout_keep_worker_lease(cancel):
    collector = local.LocalCollector(
        HostSettings(tool_timeout_seconds=0.03, queue_timeout_seconds=0)
    )
    started, release = threading.Event(), threading.Event()

    def worker():
        started.set()
        assert release.wait(2)
        return 42

    tasks = [asyncio.create_task(collector.call(worker)) for _ in range(4)]
    try:
        while not started.is_set():
            await asyncio.sleep(0.001)
        if cancel:
            for task in tasks:
                task.cancel()
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        expected = asyncio.CancelledError if cancel else (TimeoutError, asyncio.TimeoutError)
        assert all(isinstance(outcome, expected) for outcome in outcomes)
        extra = Mock()
        with pytest.raises(HostOperationError) as error:
            await collector.call(extra)
        assert error.value.code == "BUSY"
        extra.assert_not_called()
        release.set()
        while collector._active:
            await asyncio.sleep(0.001)
        assert await collector.call(lambda: 43) == 43
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await collector.aclose()
