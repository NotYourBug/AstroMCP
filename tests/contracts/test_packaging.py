from importlib.metadata import distribution


def test_independent_console_scripts():
    for kind, module in [("host", "resource_check_mcp"), ("k8s", "k8s_check_mcp")]:
        package = distribution("astromcp-" + kind)
        entry = next(e for e in package.entry_points if e.name == "astromcp-" + kind)
        assert entry.value == module + ".__main__:main"
        assert not any(
            requirement.lower().startswith("astromcp-") for requirement in package.requires or []
        )


def test_host_settings_are_immutable():
    import pytest
    from resource_check_mcp.config import load_settings

    settings = load_settings({})
    with pytest.raises(ValueError):
        settings.bind_port = 1
