import time
from types import SimpleNamespace

import pytest


class Channel:
    def __init__(self, out=b"hello", err=b"warning", done=True):
        self.out, self.err, self.done = out, err, done
        self.closed = False
    def recv_ready(self): return bool(self.out)
    def recv_stderr_ready(self): return bool(self.err)
    def recv(self, n):
        data, self.out = self.out[:n], self.out[n:]
        return data
    def recv_stderr(self, n):
        data, self.err = self.err[:n], self.err[n:]
        return data
    def exit_status_ready(self): return self.done
    def recv_exit_status(self):
        assert not self.out and not self.err, "exit status read before draining output"
        return 0
    def close(self): self.closed = True


class SSHClient:
    def __init__(self, channel): self.channel, self.closed = channel, False
    def connect(self, **kwargs): pass
    def exec_command(self, command, timeout):
        return SimpleNamespace(close=lambda: None), SimpleNamespace(channel=self.channel), None
    def close(self): self.closed = True


async def test_drains_both_streams_before_exit(legacy_module):
    import resource_check_mcp.ssh as m
    channel = Channel(out=b"a" * 100000, err=b"b" * 100000)
    client = SSHClient(channel)
    executor = m.SSHExecutor(m.load_settings({}), client_factory=lambda: client)
    result = await executor.execute(m.SSHConnection("test", 22, "user", "TEST_SECRET", None), "uname", deadline=time.monotonic()+1)
    assert result.exit_code == 0
    assert len(result.stdout) == len(result.stderr) == 100000
    assert channel.closed and client.closed
    assert "TEST_SECRET" not in repr(m.SSHConnection("test", 22, "user", "TEST_SECRET", None))
    await executor.aclose()


@pytest.mark.parametrize("mode,code", [("limit", "OUTPUT_LIMIT"), ("timeout", "TIMEOUT")])
async def test_output_and_deadline_bounds(legacy_module, mode, code):
    import resource_check_mcp.ssh as m
    channel=Channel(out=b"a"*100 if mode=="limit" else b"", err=b"", done=mode=="limit")
    client=SSHClient(channel)
    settings=m.load_settings({"MCP_SSH_OUTPUT_LIMIT_BYTES":"10"})
    executor=m.SSHExecutor(settings,client_factory=lambda:client)
    result=await executor.execute(m.SSHConnection("test",22,"user",None,None),"uname",deadline=time.monotonic()+.05)
    assert result.error_code==code
    await executor.aclose()
    assert client.closed


async def test_twenty_requests_respect_limits(legacy_module):
    import asyncio
    import threading
    import resource_check_mcp.ssh as m
    lock=threading.Lock()
    active={"all":0,"peak":0,"targets":{},"target_peak":0}
    class CountingClient(SSHClient):
        def connect(self, **kwargs):
            self.host=kwargs["hostname"]
            with lock:
                active["all"]+=1
                active["targets"][self.host]=active["targets"].get(self.host,0)+1
                active["peak"]=max(active["peak"],active["all"])
                active["target_peak"]=max(active["target_peak"],active["targets"][self.host])
            time.sleep(.01)
        def close(self):
            if not self.closed:
                with lock:
                    active["all"]-=1
                    active["targets"][self.host]-=1
            super().close()
    executor=m.SSHExecutor(m.load_settings({"MCP_SSH_MAX_IN_FLIGHT":"2","MCP_SSH_MAX_PER_TARGET":"1"}),client_factory=lambda:CountingClient(Channel()))
    results=await asyncio.gather(*(executor.execute(m.SSHConnection(f"host{i%4}",22,"u",None,None),"uname",deadline=time.monotonic()+3) for i in range(20)))
    assert all(r.exit_code==0 for r in results)
    assert active["peak"]<=2 and active["target_peak"]<=1
    assert not executor._targets
    await executor.aclose()

