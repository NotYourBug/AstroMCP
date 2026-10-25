from unittest.mock import Mock

import paramiko


def test_load_known_hosts_and_reject_unknown(legacy_module, monkeypatch):
    import resource_check_mcp.ssh as module
    client = Mock()
    monkeypatch.setattr(module.paramiko, "SSHClient", lambda: client)
    settings = module.load_settings({})
    assert module.create_ssh_client(settings) is client
    client.load_system_host_keys.assert_called_once()
    assert isinstance(client.set_missing_host_key_policy.call_args.args[0], paramiko.RejectPolicy)
