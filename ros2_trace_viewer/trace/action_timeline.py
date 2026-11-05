"""Action columns: real callback intervals and observed transport timestamps.

No goal spans, request events or causal edges are synthesized. A successful
take is a receive observation, not proof of a particular publisher's delivery.
"""
from .filters import JOIN
from dataclasses import replace
from .client_calls import add_call_markers
from .topics import callback_scope, owner_scope, publication_scope
from .action_names import resource_scope
from .publications import add_publish_markers
from .communication_timeline import density


def receive_scope(filters):
    """Receive observations have no callback identity; strict scopes exclude them."""
    where, params = owner_scope(filters, 'r.pid', 'r.node_id', 'r.tid')
    if callback_scope(filters):
        where += ' AND 0'
    resources, values = resource_scope(filters, 'r.topic', 'topic')
    where += f' AND ({resources}) AND r.timestamp_ns>=? AND r.timestamp_ns<=?'
    params += [*values, filters.start, filters.end]
    if filters.search:
        where += " AND instr(lower(r.topic||' '||coalesce(n.name,'')||' '||coalesce(p.name,'')),lower(?))>0"
        params.append(filters.search)
    source = '''topic_receives r LEFT JOIN nodes n ON n.id=r.node_id
                LEFT JOIN processes p ON p.pid=r.pid'''
    return source, where, params


def encode_point(row):
    result = dict(row)
    for key in ('timestamp_ns', 'end_ns'):
        if result.get(key) is not None:
            result[key] = str(result[key])
    return result


def point_source(filters, kind, grouping):
    """Build one filtered observation source with its requested owner column."""
    if kind == 'publish':
        source, where, params = publication_scope(filters)
        alias, identity, node = 'pub', 'pub.id', 'pn.name'
        extra = ',pub.execution_id,pub.scope_id'
    else:
        source, where, params = receive_scope(filters)
        alias, identity, node, extra = 'r', 'r.rowid', 'n.name', ''
    source += f" JOIN action_channels ac ON ac.transport='topic' AND ac.name={alias}.topic"
    groups = {
        'action': ('ac.action', 'ac.action'),
        'node': (f"coalesce({alias}.node_id,'unknown:'||{alias}.pid)",
                 f"coalesce({node},'Unknown node')||' ['||{alias}.pid||']'"),
        'process': (f'CAST({alias}.pid AS TEXT)',
                    f"coalesce(p.name,'Unknown process')||' ['||{alias}.pid||']'"),
        'thread': (f"{alias}.pid||':'||{alias}.tid",f"'Thread '||{alias}.tid||' ['||{alias}.pid||']'"),
    }
    group, label = groups[grouping]
    return source, where, params, alias, identity, node, extra, group, label


def action_event_lanes(db, filters, grouping):
    """Include owners whose action handlers have no recorded callback spans."""
    lanes = {}
    for kind in ('publish','receive'):
        source, where, params, _, _, _, _, group, label = point_source(filters,kind,grouping)
        for row in db.execute(f'SELECT {group} id,{label} label FROM {source} WHERE {where} GROUP BY {group}',params):
            lanes[row['id']] = dict(row)
    return list(lanes.values())


def action_points(db, filters, start, end, lanes, budget, bins, kind, grouping='action'):
    source, where, params, alias, identity, node, extra, group, _ = point_source(filters,kind,grouping)
    where += f''' AND {group} IN ({','.join('?' for _ in lanes)})
        AND {alias}.timestamp_ns>=? AND {alias}.timestamp_ns<=?'''
    params += [*[lane['id'] for lane in lanes], start, end]
    fields = f'''{identity} id,{alias}.pid,{alias}.tid,{alias}.timestamp_ns,{alias}.topic,
        {node} node_name,p.name process_name,{group} lane,ac.action,
        ac.role action_role,'{kind}' event_kind{extra}'''
    rows = db.execute(f'SELECT {fields} FROM {source} WHERE {where} '
                      f'ORDER BY {alias}.timestamp_ns,{identity} LIMIT ?', [*params, budget+1]).fetchall()
    if len(rows) <= budget:
        return [encode_point(row) for row in rows], 'events'
    per_lane = max(1, min(bins, budget//len(lanes)))
    step = max(1, (end-start+1)/per_lane)
    rows = db.execute(f'''SELECT {group} lane,'{kind}' event_kind,
        CASE WHEN count(DISTINCT ac.action)=1 THEN min(ac.action) END action,
        CASE WHEN count(DISTINCT ac.role)=1 THEN min(ac.role) END action_role,
        CASE WHEN count(DISTINCT {alias}.topic)=1 THEN min({alias}.topic) END topic,
        min({alias}.timestamp_ns) timestamp_ns,max({alias}.timestamp_ns) end_ns,count(*) count,
        min(?,CAST(({alias}.timestamp_ns-?)/? AS INTEGER)) bin
        FROM {source} WHERE {where} GROUP BY lane,bin''',
        [per_lane-1, start, step, *params])
    return [encode_point(row) for row in rows], 'density'


def add_action_observations(db, result, filters, grouping, start, end, budget, bins):
    """Attach bounded action activity to the ordinary owner-grouped timeline."""
    result.update(publications=[],receives=[])
    if not result['lanes'] or end < start:
        return result
    for kind, key in [('publish','publications'),('receive','receives')]:
        result[key],result[key+'_mode'] = action_points(db,filters,start,end,result['lanes'],
            max(1,budget//2),bins,kind,grouping)
    return result


def get_receive(db, receive_id, filters):
    source, where, params = receive_scope(filters)
    row = db.execute(f'''SELECT r.rowid id,r.pid,r.tid,r.topic,r.timestamp_ns,
        n.name node_name,p.name process_name,ac.action,ac.role action_role,
        'receive' event_kind FROM {source} JOIN action_channels ac
        ON ac.transport='topic' AND ac.name=r.topic WHERE ({where}) AND r.rowid=?''',
        [*params, receive_id]).fetchone()
    return encode_point(row) if row else None


def action_timeline(db, filters, all_lanes, start, end, lane_offset, lane_limit,
                    budget, bins, fields, encode):
    start, end = max(start, filters.start), min(end, filters.end)
    lanes = all_lanes[lane_offset:lane_offset+lane_limit]
    result = {'mode':'executions','lanes':lanes,'total_lanes':len(all_lanes),
        'records':[],'publications':[],'receives':[],'goal_timing_available':False,
        'receives_attributable':not callback_scope(filters),'publish_markers_mode':'events'}
    if not lanes or end < start:
        return result
    where, params = filters.sql(include_time=False)
    source = 'intervals r CROSS JOIN ' + JOIN + ' JOIN execution_actions ea ON ea.execution_id=e.id'
    where += f''' AND ea.action IN ({','.join('?' for _ in lanes)}) AND r.id=e.id
        AND e.start_ns<=? AND e.end_ns>=? AND r.start_s<=? AND r.end_s>=?'''
    params += [*[lane['id'] for lane in lanes],end,start,end/1e9,start/1e9]
    rows = db.execute(f'SELECT {fields},ea.action lane FROM {source} WHERE {where} LIMIT ?',
                      [*params,budget+1]).fetchall()
    if len(rows) <= budget:
        result['records'] = [encode(row) for row in sorted(rows,key=lambda row:(row['start_ns'],row['id']))]
        # Count and attach only this action's accepted publication channels.
        for lane in lanes:
            records = [row for row in result['records'] if row['lane']==lane['id']]
            add_call_markers(db,records,start,end,replace(filters,actions=(lane['id'],),services=('__none__',)))
            topics = tuple(row[0] for row in db.execute(
                "SELECT name FROM action_channels WHERE action=? AND transport='topic'", (lane['id'],)))
            if topics:
                mode = add_publish_markers(db,records,start,end,topics=topics,
                    count_start=filters.start,count_end=filters.end,filters=filters,
                    limit=max(1,20000//len(lanes)))
                if mode == 'density': result['publish_markers_mode'] = mode
            else:
                for row in records: row.update(publish_count=0,publishes=[])
    else:
        cells, step = density(db,source,where,params,'ea.action','e.start_ns','e.end_ns',start,end,lanes,bins)
        result.update(mode='density',density=cells,bins=bins,start_ns=str(start),step_ns=step)
    for kind, key in [('publish','publications'),('receive','receives')]:
        result[key],result[key+'_mode'] = action_points(db,filters,start,end,lanes,max(1,budget//2),bins,kind)
    return result
