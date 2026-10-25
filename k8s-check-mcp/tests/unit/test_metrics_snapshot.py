import pytest


@pytest.mark.parametrize("count",[5,101])
def test_each_scan_fetches_metrics_once(cluster,count):
    m,_,_,calls=cluster(count)
    m.cluster_summary()
    assert calls['metrics']==1
    calls['metrics']=0
    m.find_problem_nodes()
    assert calls['metrics']==1
