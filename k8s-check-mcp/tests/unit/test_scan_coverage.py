from kubernetes.client.exceptions import ApiException


def test_missing_metrics_is_incomplete(cluster):
    m,_,_,_=cluster(metrics_error=ApiException(status=404))
    result=m.cluster_summary()
    assert not result['complete']
    assert result['warnings']


def test_health_is_bounded(cluster):
    m,_,_,calls=cluster(5)
    result=m.health_check()
    assert calls['list'][0].get('limit')==1
    assert result['node_count'] is None
