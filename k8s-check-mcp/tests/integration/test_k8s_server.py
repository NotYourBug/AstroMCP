from datetime import datetime,timezone
from types import SimpleNamespace as N
from unittest.mock import Mock

from fastmcp import Client


async def test_k8s_factory_isolates_clients():
    from k8s_check_mcp.client import K8sGateway
    from k8s_check_mcp.config import load_settings
    from k8s_check_mcp.server import create_server
    node=N(metadata=N(name='node',creation_timestamp=datetime.now(timezone.utc)),spec=N(taints=[],unschedulable=False),
           status=N(conditions=[N(type='Ready',status='True')],addresses=[],node_info=None,capacity={'cpu':'4','memory':'8Gi'},allocatable={'cpu':'4','memory':'8Gi'}))
    def server(name):
        settings=load_settings({})
        clients=N(display_name=name,api_client=Mock(),core=N(list_node=lambda **kw:N(items=[node],metadata=N(_continue=None))))
        return create_server(settings,gateway=K8sGateway(settings,client_factory=lambda:clients))
    first,second=server('first'),server('second')
    async with Client(first) as client:
        assert len(await client.list_tools())==7
        assert len(await client.list_resources())==1
        assert len(await client.list_prompts())==2
        result=await client.call_tool('list_nodes',{})
        assert result.data['cluster']=='first'
    async with Client(second) as client:
        result=await client.call_tool('list_nodes',{})
        assert result.data['cluster']=='second'


def test_pure_resource_module_has_no_mcp_dependency():
    from k8s_check_mcp.quantities import cpu_to_millicores
    assert cpu_to_millicores('250m')==250
