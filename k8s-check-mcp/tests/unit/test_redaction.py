from k8s_check_mcp import presentation as m
def test_common_secrets_and_partial_private_key(legacy_module):
    text='password=TEST_SECRET\nAuthorization: Bearer TEST_TOKEN\n-----BEGIN PRIVATE KEY-----\nTEST_KEY'
    result=m.redact_common_secrets(text)
    assert all(value not in result for value in ['TEST_SECRET','TEST_TOKEN','TEST_KEY'])
    assert m.redact_common_secrets('normal business text')=='normal business text'
