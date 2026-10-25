from __future__ import annotations

import threading, time
from .models import K8sOperationError

class TokenBucketLimiter:

    def __init__(self, rps, burst, max_in_flight, *, queue_timeout=2, enabled=True, clock=time.monotonic):
        self._rps, self._capacity = (rps, burst)
        self._tokens = float(burst)
        self._clock = clock
        self._updated_at = clock()
        self._lock = threading.Lock()
        self._sem = threading.BoundedSemaphore(max_in_flight)
        self.queue_timeout, self.enabled = (queue_timeout, enabled)

    def acquire(self, *, deadline):
        now = self._clock()
        if now >= deadline:
            raise K8sOperationError('TIMEOUT', 'Operation deadline exceeded')
        end = min(deadline, now + self.queue_timeout)
        if not self._sem.acquire(timeout=max(0, end - now)):
            raise K8sOperationError('BUSY', 'API concurrency budget exhausted')
        try:
            while self.enabled:
                with self._lock:
                    now = self._clock()
                    self._tokens = min(self._capacity, self._tokens + max(now - self._updated_at, 0) * self._rps)
                    self._updated_at = now
                    if self._tokens >= 1:
                        self._tokens -= 1
                        return
                    wait = (1 - self._tokens) / self._rps
                if now >= end:
                    raise K8sOperationError('BUSY', 'API rate-limit queue budget exhausted')
                time.sleep(min(wait, max(0, end - now), 0.05))
        except BaseException:
            self._sem.release()
            raise

    def release(self):
        self._sem.release()
