from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import psutil

from ..models import _TOOL_DEADLINE, HostOperationError
from ._common import _memory_fields, _percent


class LocalCollector:
    def __init__(self, settings):
        self.settings = settings
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="astromcp-local")
        self._sem = threading.BoundedSemaphore(4)
        self._active = set()

    async def call(self, fn, *args, **kwargs):
        deadline = _TOOL_DEADLINE.get() or time.monotonic() + self.settings.tool_timeout_seconds
        queue_end = min(deadline, time.monotonic() + self.settings.queue_timeout_seconds)
        while not self._sem.acquire(blocking=False):
            if time.monotonic() >= queue_end:
                raise HostOperationError("BUSY", "Local collector workers are busy")
            await asyncio.sleep(0.005)
        future = self._pool.submit(fn, *args, **kwargs)
        self._active.add(future)

        def finished(done):
            self._active.discard(done)
            self._sem.release()

        future.add_done_callback(finished)
        wrapped = asyncio.wrap_future(future)
        wrapped.add_done_callback(lambda f: f.exception() if not f.cancelled() else None)
        return await asyncio.wait_for(
            asyncio.shield(wrapped), max(deadline - time.monotonic(), 0.001)
        )

    async def collect_cpu(self):
        return {
            "percent": _percent(await self.call(psutil.cpu_percent, interval=1)),
            "sample_window_seconds": 1,
        }

    async def collect_memory(self):
        memory = await self.call(psutil.virtual_memory)
        return _memory_fields(memory.total, memory.available)

    async def collect_disk(self, path, threshold):
        usage = await self.call(psutil.disk_usage, path)
        return {
            "total_gb": round(usage.total / 1024**3, 2),
            "used_gb": round(usage.used / 1024**3, 2),
            "free_gb": round(usage.free / 1024**3, 2),
            "percent": _percent(usage.percent),
            "path": path,
            "threshold": threshold,
            "warning": usage.percent > threshold,
        }

    async def collect_tcp(self):
        return {"tcp_count": len(await self.call(psutil.net_connections, kind="tcp"))}

    async def aclose(self):
        active = list(self._active)
        if active:
            await asyncio.wait([asyncio.wrap_future(f) for f in active], timeout=1)
        self._pool.shutdown(wait=False, cancel_futures=True)
