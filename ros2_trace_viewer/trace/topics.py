"""Topic metadata and observed activity, without claiming individual deliveries."""
from dataclasses import replace

from .filters import JOIN, MAX_NS


def owner_scope(filters, pid, node, tid=None):
    clauses, params = [], []
    if filters.none:
        clauses.append('0')
    columns = {'processes': pid, 'nodes': f"coalesce({node},'unknown:'||{pid})"}
    if tid:
        columns['threads'] = tid
    for name, column in columns.items():
        for prefix, operator in [('', 'IN'), ('exclude_', 'NOT IN')]:
            values = getattr(filters, prefix + name, ())
            if values:
                clauses.append(f'{column} {operator} ({",".join("?" for _ in values)})')
                params.extend(values)
    if filters.include_selection:
        selected = []
        for name in ('processes', 'nodes'):
            values = getattr(filters, 'include_' + name)
            if values:
                selected.append(f'{columns[name]} IN ({",".join("?" for _ in values)})')
                params.extend(values)
        clauses.append('(' + ' OR '.join(selected) + ')' if selected else '0')
    return ' AND '.join(clauses) or '1', params


def callback_scope(filters):
    return bool(filters.callbacks or filters.exclude_callbacks or filters.types or
                filters.include_callbacks or filters.min_duration or
                filters.max_duration != MAX_NS or filters.cpus)


def publication_scope(filters):
    """A publication predicate shared by topic statistics and topic columns."""
    original_search = filters.search
    strict = callback_scope(filters)
    base, params = replace(filters, search='', topics=()).sql()
    direct, direct_params = owner_scope(filters, 'pub.pid', 'pub.node_id', 'pub.tid')
    where = f'(pub.execution_id IS NOT NULL AND ({base}))'
    if not strict:
        where += f' OR (pub.execution_id IS NULL AND ({direct}))'
        params += direct_params
    where = f'({where}) AND pub.timestamp_ns>=? AND pub.timestamp_ns<=?'
    params += [filters.start, filters.end]
    source = '''publications pub LEFT JOIN executions e ON e.id=pub.execution_id
        LEFT JOIN callbacks c ON c.id=e.callback_id LEFT JOIN nodes n ON n.id=c.node_id
        LEFT JOIN nodes pn ON pn.id=pub.node_id LEFT JOIN processes p ON p.pid=pub.pid'''
    if original_search:
        where += " AND instr(lower(coalesce(pub.topic,'')||' '||coalesce(pn.name,'')||' '||coalesce(n.name,'')||' '||coalesce(c.topic,'')||' '||coalesce(c.service,'')||' '||coalesce(c.symbol,'')||' '||coalesce(p.name,'')),lower(?))>0"
        params.append(original_search)
    if filters.topics:
        where += f' AND pub.topic IN ({",".join("?" for _ in filters.topics)})'
        params.extend(filters.topics)
    return source, where, params


def topic_data(db, filters, trace_end, search='', offset=0, limit=50, topic=None):
    """Counts use selected timestamps; metadata lists recorded same-topic endpoints.

    Publish activity follows callback filters when attribution exists. Receive
    counts become unknown under callback/duration/CPU filters because rmw_take
    does not identify a callback. Topic associations are declarations, not proof
    of QoS compatibility or delivery. Empty activity is valid recorded topology.
    """
    filters = replace(filters, end=min(filters.end, trace_end))
    strict = callback_scope(filters)
    original_search = filters.search
    source, where, params = publication_scope(filters)
    published = {row['topic']: dict(row) for row in db.execute(f'''SELECT pub.topic,
        count(*) publish_count,min(pub.timestamp_ns) first_ns,max(pub.timestamp_ns) last_ns
        FROM {source} WHERE {where} AND pub.topic IS NOT NULL GROUP BY pub.topic''', params)}
    received = {}
    if not strict:
        condition, values = owner_scope(filters, 'r.pid', 'r.node_id', 'r.tid')
        if original_search:
            condition += " AND instr(lower(r.topic||' '||coalesce(n.name,'')||' '||coalesce(p.name,'')),lower(?))>0"
            values.append(original_search)
        received = {row['topic']: dict(row) for row in db.execute(f'''SELECT r.topic,
            count(*) receive_count,min(r.timestamp_ns) first_ns,max(r.timestamp_ns) last_ns
            FROM topic_receives r LEFT JOIN nodes n ON n.id=r.node_id
            LEFT JOIN processes p ON p.pid=r.pid WHERE {condition}
            AND r.timestamp_ns>=? AND r.timestamp_ns<=? GROUP BY r.topic''',
            [*values, filters.start, filters.end])}
    cb_where, cb_params = filters.sql('contained')
    callbacks = {row['topic']: dict(row) for row in db.execute(f'''SELECT c.topic,
        count(DISTINCT c.id) callback_count,count(*) execution_count,
        sum_ns(e.duration_ns) total_callback_ns,avg(e.duration_ns) mean_duration_ns
        FROM {JOIN} WHERE {cb_where} AND c.type='subscription' AND c.topic IS NOT NULL
        GROUP BY c.topic''', cb_params)}
    scope, scope_params = owner_scope(filters, 'ep.pid', 'ep.node_id')
    all_endpoints = {}
    scoped = set()
    for row in db.execute('''SELECT ep.*,n.name node_name,p.name process_name FROM endpoints ep
        LEFT JOIN nodes n ON n.id=ep.node_id LEFT JOIN processes p ON p.pid=ep.pid
        WHERE ep.created_ns<=? ORDER BY ep.topic,ep.kind,n.name,ep.id''', (filters.end,)):
        item = dict(row)
        item['created_ns'] = str(item['created_ns'])
        all_endpoints.setdefault(item['topic'], []).append(item)
    if not strict:
        condition = scope
        if original_search:
            condition += " AND instr(lower(ep.topic||' '||coalesce(n.name,'')||' '||coalesce(p.name,'')),lower(?))>0"
            scope_params.append(original_search)
        scoped = {row[0] for row in db.execute(f'''SELECT DISTINCT ep.topic FROM endpoints ep
            LEFT JOIN nodes n ON n.id=ep.node_id LEFT JOIN processes p ON p.pid=ep.pid
            WHERE {condition} AND ep.created_ns<=?''', [*scope_params, filters.end])}
    candidates = set(published) | set(received) | set(callbacks) | scoped
    candidates.discard(None)
    if filters.topics:
        candidates &= set(filters.topics)
    if filters.none:
        candidates.clear()
    if topic is not None:
        candidates &= {topic}
    if search:
        candidates = {name for name in candidates if search.casefold() in name.casefold()}
    interval = max(0, filters.end - filters.start)
    result = []
    for name in candidates:
        pub, rec, cb = published.get(name, {}), received.get(name, {}), callbacks.get(name, {})
        endpoints = all_endpoints.get(name, [])
        publishers = [item for item in endpoints if item['kind'] == 'publisher']
        subscribers = [item for item in endpoints if item['kind'] == 'subscription']
        first = [item['first_ns'] for item in (pub, rec) if item.get('first_ns') is not None]
        last = [item['last_ns'] for item in (pub, rec) if item.get('last_ns') is not None]
        result.append({'topic': name, 'publish_count': pub.get('publish_count', 0),
            'receive_count': None if strict else rec.get('receive_count', 0),
            'publish_rate_hz': pub.get('publish_count', 0) * 1e9 / interval if interval else None,
            'publisher_count': len(publishers), 'subscription_count': len(subscribers),
            'publisher_nodes': sorted({x['node_name'] or 'Unknown node' for x in publishers})[:8],
            'subscriber_nodes': sorted({x['node_name'] or 'Unknown node' for x in subscribers})[:8],
            'callback_count': cb.get('callback_count', 0), 'execution_count': cb.get('execution_count', 0),
            'total_callback_ns': cb.get('total_callback_ns', '0'),
            'mean_duration_ns': cb.get('mean_duration_ns'),
            'first_ns': str(min(first)) if first else None, 'last_ns': str(max(last)) if last else None,
            **({'publishers': publishers[:100], 'subscriptions': subscribers[:100]} if topic else {})})
    result.sort(key=lambda item: (-item['publish_count'], item['topic']))
    return {'items': result[offset:offset + limit], 'total': len(result), 'offset': offset,
            'limit': limit, 'receives_attributable': not strict,
            'association': 'Same recorded topic; delivery and QoS compatibility are not verified.'}
