import pytest


@pytest.mark.parametrize("env", [{"MCP_K8S_VERIFY_SSL":"tru"},{"MCP_THRESHOLD_NODE_CPU_UTILIZATION":"NaN"},{"MCP_K8S_API_RPS":"0"},{"MCP_THRESHOLD_POD_RESTART_COUNT":"-1"}])
def test_invalid_settings_rejected(legacy_module,env):
    with pytest.raises(ValueError):legacy_module("k8s").load_settings(env)


def test_env_overrides_config(legacy_module,tmp_path):
    config=tmp_path/'config.json';config.write_text('{"thresholds":{"node_cpu_utilization":0.2}}')
    settings=legacy_module("k8s").load_settings({"MCP_THRESHOLD_NODE_CPU_UTILIZATION":"0.9"},config)
    assert settings.thresholds.node_cpu_utilization==.9
    assert settings.verify_ssl


def test_explicit_missing_config_rejected(legacy_module,tmp_path):
    with pytest.raises((ValueError,FileNotFoundError)):legacy_module("k8s").load_settings({},tmp_path/'missing.json')
