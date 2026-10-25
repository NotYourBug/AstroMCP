import resource_check_mcp.collectors.macos as macos
import resource_check_mcp.collectors.windows as windows
import pytest


def test_mac_cpu_includes_system_time(legacy_module):
    import resource_check_mcp.collectors as m
    assert m.parse_cpu_mac("CPU usage: 12.3% user, 7.7% sys, 80.0% idle")==20


def test_mac_uses_last_sample(legacy_module):
    text="CPU usage: 1% user, 0% sys, 99% idle\nCPU usage: 12.3% user, 7.7% sys, 80% idle"
    assert macos.parse_cpu_mac(text)==20


def test_linux_memory_uses_available(legacy_module):
    import resource_check_mcp.collectors as m
    result=m.parse_memory_linux("total used free shared buff/cache available\nMem: 1000 300 100 0 600 700\nSwap: 0 0 0")
    assert result["percent"]==30


@pytest.mark.parametrize("text",["null","{}",'{"LoadPercentage":"bad"}',"[]"])
def test_bad_windows_cpu_not_zero(legacy_module,text):
    with pytest.raises(ValueError): windows.parse_cpu_windows(text)


def test_windows_multi_cpu(legacy_module):
    assert windows.parse_cpu_windows('[{"LoadPercentage":20},{"LoadPercentage":40}]')==30


async def test_command_failure_not_successful_zero():
    import time
    from resource_check_mcp import collectors
    from resource_check_mcp.models import CommandResult, SSHConnection
    class Executor:
        async def execute(self,*args,**kwargs):return CommandResult(127,"","command not found")
    result=await collectors.collect_tcp("linux",SSHConnection("host",22,"u"),Executor(),time.monotonic()+1)
    assert not result["ok"]
    assert "tcp_count" not in result
