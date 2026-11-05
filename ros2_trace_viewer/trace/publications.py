"""Bounded publication queries for inspector lists and Canvas markers."""
from .filters import MAX_NS
from .topics import publication_scope

FIELDS = '''pub.*,n.name node_name,p.name process_name'''
SOURCE = '''publications pub LEFT JOIN nodes n ON n.id=pub.node_id
            LEFT JOIN processes p ON p.pid=pub.pid'''
LAYER_NAMES = [(1, 'ros2:rclcpp_publish'), (2, 'ros2:rcl_publish'),
               (4, 'ros2:rmw_publish'), (8, 'ros2:rclcpp_intra_publish')]


def publication_dict(row):
    result = dict(row)
    for key in ('timestamp_ns', 'source_timestamp', 'end_ns'):
        if result.get(key) is not None:
            result[key] = str(result[key])
    if 'layers' in result:
        result['tracepoints'] = [name for mask, name in LAYER_NAMES if result['layers'] & mask]
    result['callback_status'] = ('complete' if result.get('execution_id') is not None else
                                 'incomplete' if result.get('scope_id') is not None else 'outside')
    return result


def get_publication(db, publication_id, filters=None):
    if filters is None:
        fields, source, where, params = FIELDS, SOURCE, '1', []
    else:
        source, where, params = publication_scope(filters)
        fields = 'pub.*,pn.name node_name,p.name process_name'
    row = db.execute(f'SELECT {fields} FROM {source} WHERE ({where}) AND pub.id=?', [*params,publication_id]).fetchone()
    return publication_dict(row) if row else None


def count_publications(db, execution_id, filters=None):
    if filters is not None:
        source, where, params = publication_scope(filters)
        return db.execute(f'SELECT count(*) FROM {source} WHERE ({where}) AND pub.execution_id=?',
                          [*params, execution_id]).fetchone()[0]
    total = db.execute('SELECT count FROM publication_counts WHERE execution_id=?',
                       (execution_id,)).fetchone()
    return total[0] if total else 0


def list_publications(db, execution_id, offset=0, limit=20, filters=None):
    total = count_publications(db, execution_id, filters)
    if filters is None:
        fields, source, where, params = FIELDS, SOURCE, '1', []
    else:
        source, where, params = publication_scope(filters)
        fields = 'pub.*,pn.name node_name,p.name process_name'
    rows = db.execute(f'''SELECT {fields} FROM {source} WHERE ({where}) AND pub.execution_id=?
        ORDER BY pub.timestamp_ns,pub.id LIMIT ? OFFSET ?''', [*params,execution_id,limit,offset])
    return {'items': [publication_dict(row) for row in rows], 'total': total,
            'offset': offset, 'limit': limit}


def add_publish_markers(db, records, start, end, limit=20000, topics=(), topic_lanes=False,
                        count_start=0, count_end=MAX_NS, filters=None):
    """Keep all individual markers when bounded; aggregate crowded bins otherwise.

    Each density marker retains an exact count and first/last recorded timestamp.
    The number of returned bins is bounded across all visible executions, not
    just per callback. CPU segments reuse their execution's markers and clip in UI.
    """
    ids = sorted({row['id'] for row in records})
    if not ids:
        return 'events'
    placeholders = ','.join('?' for _ in ids)
    searching = bool(filters and (filters.search or filters.actions or filters.topics))
    # Global topic choices belong to Topics; action transport has its own
    # selection. Explicit lane topics still restrict markers to their column.
    if filters is not None and not topic_lanes and topics == filters.topics:
        topics = ()
    if searching:
        source, scope, scope_params = publication_scope(filters)
        count_source, node = source, 'pn.name'
    else:
        source, count_source, node = SOURCE, 'publications pub', 'n.name'
        scope, scope_params = '1', []
    topic_counts = {}
    if topics or topic_lanes or count_start or count_end != MAX_NS or searching:
        count_where = f'pub.execution_id IN ({placeholders}) AND pub.timestamp_ns>=? AND pub.timestamp_ns<=?'
        count_params = [*ids,count_start,count_end]
        if topics:
            count_where += f' AND pub.topic IN ({",".join("?" for _ in topics)})'
            count_params.extend(topics)
        count_where += f' AND ({scope})'
        count_params.extend(scope_params)
        counts = {}
        for row in db.execute(f'SELECT pub.execution_id,pub.topic,count(*) total FROM {count_source} '
                              f'WHERE {count_where} GROUP BY pub.execution_id,pub.topic', count_params):
            counts[row['execution_id']] = counts.get(row['execution_id'],0)+row['total']
            topic_counts[(row['execution_id'],row['topic'])] = row['total']
    else:
        counts = dict(db.execute(f'SELECT execution_id,count FROM publication_counts '
                                 f'WHERE execution_id IN ({placeholders})', ids))
    markers = {execution_id: [] for execution_id in ids}
    where = f'pub.execution_id IN ({placeholders}) AND pub.timestamp_ns>=? AND pub.timestamp_ns<=?'
    params = [*ids, start, end]
    if topics:
        where += f' AND pub.topic IN ({",".join("?" for _ in topics)})'
        params.extend(topics)
    where += f' AND ({scope})'
    params.extend(scope_params)
    found = db.execute(f'''SELECT pub.id,pub.execution_id,pub.timestamp_ns,pub.topic,
        {node} node_name FROM {source}
        WHERE {where} ORDER BY pub.execution_id,pub.timestamp_ns,pub.id LIMIT ?''',
        [*params, limit + 1]).fetchall()
    mode = 'events'
    if len(found) > limit:
        mode = 'density'
        bins = max(1, min(256, limit // len(ids)))
        step = max(1, (end - start + 1) / bins)
        found = db.execute(f'''SELECT pub.execution_id,min(pub.timestamp_ns) timestamp_ns,
            max(pub.timestamp_ns) end_ns,count(*) count,
            CASE WHEN count(DISTINCT pub.topic)=1 THEN min(pub.topic) END topic,
            CASE WHEN count(DISTINCT {node})=1 THEN min({node}) END node_name,
            CAST((pub.timestamp_ns-?)/? AS INTEGER) bin
            FROM {source} WHERE {where}
            GROUP BY pub.execution_id,bin''', [start, step, *params]).fetchall()
    for row in found:
        marker = dict(row)
        marker['timestamp_ns'] = str(marker['timestamp_ns'])
        if 'end_ns' in marker:
            marker['end_ns'] = str(marker['end_ns'])
        markers[marker['execution_id']].append(marker)
    for row in records:
        row['publish_count'] = counts.get(row['id'], 0)
        if topic_lanes:
            row['topic_publish_count'] = topic_counts.get((row['id'],row['lane']),0)
        row['publishes'] = ([m for m in markers[row['id']] if m['topic']==row['lane']]
                            if topic_lanes else markers[row['id']])
    return mode
