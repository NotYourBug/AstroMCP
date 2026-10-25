import pytest


def test_mac_cpu_includes_system_time(legacy_module):
    m=legacy_module("host")
    assert m.parse_cpu_mac("CPU usage: 12.3% user, 7.7% sys, 80.0% idle")==20


def test_mac_uses_last_sample(legacy_module):
    text="CPU usage: 1% user, 0% sys, 99% idle\nCPU usage: 12.3% user, 7.7% sys, 80% idle"
    assert legacy_module("host").parse_cpu_mac(text)==20


def test_linux_memory_uses_available(legacy_module):
    m=legacy_module("host")
    result=m.parse_memory_linux("total used free shared buff/cache available\nMem: 1000 300 100 0 600 700\nSwap: 0 0 0")
    assert result["percent"]==30


@pytest.mark.parametrize("text",["null","{}",'{"LoadPercentage":"bad"}',"[]"])
def test_bad_windows_cpu_not_zero(legacy_module,text):
    with pytest.raises(ValueError): legacy_module("host").parse_cpu_windows(text)


def test_windows_multi_cpu(legacy_module):
    assert legacy_module("host").parse_cpu_windows('[{"LoadPercentage":20},{"LoadPercentage":40}]')==30


async def test_command_failure_not_successful_zero(legacy_module,monkeypatch):
    m=legacy_module("host")
    async def remote(*args):return 127,"","command not found"
    monkeypatch.setattr(m,"run_remote_command",remote)
    result=await m._tcp_remote("linux","host",22,"user",None,None)
    assert not result["ok"]
    assert "tcp_count" not in result
