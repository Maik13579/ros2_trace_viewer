"""Recorded service declarations and server callback execution statistics."""
from dataclasses import replace
from .filters import JOIN
from .topics import callback_scope, owner_scope
from .service_names import is_default_service
from .action_names import accepts_resource, action_channel


def service_data(db, filters, trace_end, offset=0, limit=50, service=None, include_actions=False):
    filters = replace(filters, end=min(filters.end, trace_end))
    where, params = filters.sql('contained')
    metrics = {row['service']: dict(row) for row in db.execute(f'''SELECT c.service,
        count(*) execution_count,count(DISTINCT c.id) callback_count,
        sum_ns(e.duration_ns) total_callback_ns,avg(e.duration_ns) mean_duration_ns,
        min(e.duration_ns) min_duration_ns,max(e.duration_ns) max_duration_ns,
        min(e.start_ns) first_ns,max(e.end_ns) last_ns FROM {JOIN}
        WHERE {where} AND c.type='service' AND c.service IS NOT NULL GROUP BY c.service''', params)}
    endpoints = {}
    for row in db.execute('''SELECT ep.*,n.name node_name,p.name process_name FROM service_endpoints ep
        LEFT JOIN nodes n ON n.id=ep.node_id LEFT JOIN processes p ON p.pid=ep.pid
        WHERE ep.created_ns<=? ORDER BY ep.name,ep.kind,n.name,ep.id''', (filters.end,)):
        item = dict(row); item['created_ns'] = str(item['created_ns'])
        endpoints.setdefault(item['name'], []).append(item)
    candidates = set(metrics)
    strict = callback_scope(filters)
    if not strict:
        scope, values = owner_scope(filters, 'ep.pid', 'ep.node_id')
        if filters.search:
            scope += " AND instr(lower(ep.name||' '||coalesce(n.name,'')||' '||coalesce(p.name,'')),lower(?))>0"
            values.append(filters.search)
        candidates |= {r[0] for r in db.execute(f'''SELECT DISTINCT ep.name FROM service_endpoints ep
            LEFT JOIN nodes n ON n.id=ep.node_id LEFT JOIN processes p ON p.pid=ep.pid
            WHERE {scope} AND ep.created_ns<=?''', [*values, filters.end])}
    candidates = {name for name in candidates if accepts_resource(filters, name, 'service')}
    if not include_actions and service is None:
        candidates = {name for name in candidates if not action_channel(name, 'service')}
    if filters.exclude_default_services:
        candidates = {name for name in candidates if not is_default_service(name)}
    if service is not None:
        candidates &= {service}
    if filters.none:
        candidates.clear()
    interval = max(0, filters.end - filters.start)
    rows = []
    for name in candidates:
        metric = metrics.get(name, {})
        servers = [e for e in endpoints.get(name, []) if e['kind'] == 'server']
        clients = [e for e in endpoints.get(name, []) if e['kind'] == 'client']
        rows.append({'service': name, 'execution_count': metric.get('execution_count', 0),
            'callback_count': metric.get('callback_count', 0),
            'total_callback_ns': metric.get('total_callback_ns', '0'),
            'mean_duration_ns': metric.get('mean_duration_ns'),
            'min_duration_ns': str(metric['min_duration_ns']) if metric else None,
            'max_duration_ns': str(metric['max_duration_ns']) if metric else None,
            'first_ns': str(metric['first_ns']) if metric else None,
            'last_ns': str(metric['last_ns']) if metric else None,
            'callback_rate_hz': metric.get('execution_count', 0)*1e9/interval if interval else None,
            'server_count': len(servers), 'client_count': len(clients),
            'server_nodes': sorted({e['node_name'] or 'Unknown node' for e in servers})[:8],
            'client_nodes': sorted({e['node_name'] or 'Unknown node' for e in clients})[:8],
            **({'servers': servers[:100], 'clients': clients[:100]} if service else {})})
    rows.sort(key=lambda row: (-row['execution_count'], row['service']))
    return {'items': rows[offset:offset+limit], 'total': len(rows), 'offset': offset, 'limit': limit,
            'request_response_timing': False,
            'caller_callback_attribution': False,
            'client_association': 'Recorded client declarations; individual calls and their calling callbacks are not identified by standard Jazzy tracepoints.',
            'timing': 'Observed server callback wall-clock durations; request/reply transport timing is not recorded.'}
