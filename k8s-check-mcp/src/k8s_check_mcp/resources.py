from __future__ import annotations

from .models import PodResources, _value, _container_statuses
from .quantities import cpu_to_millicores, memory_to_bytes, parse_quantity_decimal

def _resource_pair(resources, section):
    values = _value(resources, section) or {}
    return (cpu_to_millicores(str(_value(values, 'cpu', '0'))), memory_to_bytes(str(_value(values, 'memory', '0'))))

def _add_pair(a, b):
    return tuple((None if x is None or y is None else x + y for x, y in zip(a, b)))

def _max_pair(a, b):
    return tuple((None if x is None or y is None else max(x, y) for x, y in zip(a, b)))

def _container_pair(container, section, configured=False):
    resources = _value(container, 'resources')
    pair = _resource_pair(resources, section)
    if section == 'requests' or configured:
        return pair
    values = _value(resources, 'limits') or {}
    return tuple((value if _value(values, key) is not None and value > 0 else None for key, value in zip(('cpu', 'memory'), pair)))

def _effective_pair(spec, section, configured=False):
    app = (0, 0)
    for container in _value(spec, 'containers') or []:
        app = _add_pair(app, _container_pair(container, section, configured))
    running, peak = ((0, 0), (0, 0))
    for container in _value(spec, 'init_containers') or []:
        pair = _container_pair(container, section, configured)
        if _value(container, 'restart_policy') == 'Always':
            running = _add_pair(running, pair)
            stage = running
        else:
            stage = _add_pair(running, pair)
        peak = _max_pair(peak, stage)
    effective = _max_pair(peak, _add_pair(app, running))
    overhead = _value(spec, 'overhead') or {}
    extra = (cpu_to_millicores(str(_value(overhead, 'cpu', '0'))), memory_to_bytes(str(_value(overhead, 'memory', '0'))))
    return _add_pair(effective, extra)

def effective_resources(pod) -> PodResources:
    raw = getattr(pod, '_astromcp_raw', None)
    source = raw if raw is not None else pod
    spec = _value(source, 'spec')
    warnings = []
    if spec is None or not _value(spec, 'containers') or _value(spec, 'resources'):
        warnings.append('UNSUPPORTED_RESOURCE_MODEL')
    status = _value(source, 'status')
    if _value(status, 'resize'):
        warnings.append('UNSUPPORTED_RESOURCE_MODEL')
    declared = {_value(c, 'name'): _value(_value(c, 'resources'), 'requests') or {} for c in (_value(spec, 'containers') or []) + (_value(spec, 'init_containers') or [])}
    for cs, _ in _container_statuses(source):
        allocated = _value(cs, 'allocated_resources') or _value(_value(cs, 'resources'), 'requests')
        if allocated:
            request = declared.get(_value(cs, 'name'), {})
            for resource in ('cpu', 'memory'):
                value = _value(allocated, resource)
                if value is not None and parse_quantity_decimal(value) != parse_quantity_decimal(_value(request, resource, '0')):
                    warnings.append('UNSUPPORTED_RESOURCE_MODEL')
    req = _effective_pair(spec, 'requests')
    limit = _effective_pair(spec, 'limits')
    configured = _effective_pair(spec, 'limits', True)
    return PodResources(req[0], req[1], limit[0], limit[1], not warnings, list(dict.fromkeys(warnings)), configured[0], configured[1])

def resource_advice(pods: list[dict], totals: dict) -> list[str]:
    if not totals.get('requests_overcommit'):
        return []
    advice = []
    for resource in ('cpu_request_m', 'memory_request_bytes'):
        contributors = sorted((pod for pod in pods if pod.get(resource, 0) > 0), key=lambda pod: (-pod[resource], pod.get('name', '')))[:5]
        if contributors:
            advice.append(resource + ' contributors: ' + ', '.join((f"{pod.get('namespace', '')}/{pod['name']}={pod[resource]}" for pod in contributors)))
    advice.append('Review declared requests, actual usage and allocatable capacity before changing workloads.')
    return advice

def _pod_effective_resources(pod):
    return effective_resources(pod)
