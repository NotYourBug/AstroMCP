from k8s_check_mcp import limiter as m
import time

import pytest


def test_limiter_queue_is_bounded(legacy_module):
    limiter=m.TokenBucketLimiter(5,10,1,queue_timeout=.01)
    limiter.acquire(deadline=time.monotonic()+1)
    try:
        with pytest.raises(m.K8sOperationError):limiter.acquire(deadline=time.monotonic()+1)
    finally:limiter.release()
    limiter.acquire(deadline=time.monotonic()+1)
    limiter.release()
