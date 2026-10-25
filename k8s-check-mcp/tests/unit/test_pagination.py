import time
from types import SimpleNamespace as N


def test_public_list_passes_continuation(cluster):
    m, gateway, nodes, _ = cluster(2)
    calls = []

    def page(**kwargs):
        calls.append(kwargs)
        return N(items=[nodes[1]], metadata=N(_continue="after"))

    gateway.clients.core.list_node = page
    result = m.list_nodes(limit=1, continue_token="before")
    assert calls[0]["_continue"] == "before"
    assert result["count"] == 1 and result["continue_token"] == "after"


def test_repeat_token_stops_without_duplicate_page(cluster):
    m, gateway, _, _ = cluster()

    def page(**kwargs):
        return N(items=[1], metadata=N(_continue="repeat"))

    pages = list(gateway.iter_pages(page, deadline=time.monotonic() + 1))
    assert sum(len(p.items) for p in pages) == 1
    assert not pages[-1].complete


def test_scan_result_limit_visible(cluster):
    m, gateway, nodes, _ = cluster(5)
    for node in nodes:
        node.status.conditions[0].status = "False"
    result = m.find_problem_nodes(max_results=2)
    assert len(result["items"]) == 2
    assert result["scanned_count"] == 5 and result["matched_count"] == 5
    assert result["output_truncated"] and not result["complete"]
