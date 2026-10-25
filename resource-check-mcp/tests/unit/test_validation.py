import shlex

import pytest
import resource_check_mcp.collectors as host_collectors


@pytest.mark.parametrize("path", ["/data\nwhoami", "/data\rtest", "/data\0test", "-x"])
def test_unsafe_path_rejected(legacy_module, path):
    import resource_check_mcp.collectors as module

    with pytest.raises(ValueError):
        module.build_unix_disk_command(path)


@pytest.mark.parametrize("path", ["/data files/业务", "/data/O'Reilly", "/"])
def test_path_is_one_argument(legacy_module, path):
    assert shlex.split(host_collectors.build_unix_disk_command(path)) == ["df", "-kP", path]


def test_windows_drive_validation(legacy_module):
    import resource_check_mcp.collectors as module

    assert "-DriveLetter C" in module.build_windows_disk_command("C:\\data")
    with pytest.raises(ValueError):
        module.build_windows_disk_command("\\\\server\\share")
