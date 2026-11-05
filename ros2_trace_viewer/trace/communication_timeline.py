"""Topic/service columns with observed callback intervals and publication points.

These are simultaneous observations, not message-flow or request/reply edges.
"""
from dataclasses import replace

from .filters import JOIN
from .publications import add_publish_markers, publication_dict
from .topics import callback_scope, owner_scope, publication_scope
from .service_names import is_default_service
from .action_names import action_channel


def communication_lanes(db, filters, grouping):
    field, kind = ('topic', 'subscription') if grouping == 'topic' else ('service', 'service')
    where, params = filters.sql(include_time=False)
    names = {r[0] for r in db.execute(f'SELECT DISTINCT c.{field} FROM {JOIN} WHERE {where} '
             f'AND c.type=? AND c.{field} IS NOT NULL', [*params, kind])}
    if grouping == 'topic':
        source, pub_where, pub_params = publication_scope(filters)
        names |= {r[0] for r in db.execute(f'SELECT DISTINCT pub.topic FROM {source} '
                  f'WHERE {pub_where} AND pub.topic IS NOT NULL', pub_params)}
    table, column = ('endpoints', 'topic') if grouping == 'topic' else ('service_endpoints', 'name')
    strict = callback_scope(replace(filters, services=() if grouping == 'service' else filters.services))
    selected = filters.topics if grouping == 'topic' else filters.services
    # Default columns represent recorded activity. An explicitly selected,
    # declared resource may still have an empty column in this interval.
    if selected and not strict:
        scope, values = owner_scope(filters, 'ep.pid', 'ep.node_id')
        if filters.search:
            scope += f" AND instr(lower(ep.{column}||' '||coalesce(n.name,'')||' '||coalesce(p.name,'')),lower(?))>0"
            values.append(filters.search)
        names |= {r[0] for r in db.execute(f'''SELECT DISTINCT ep.{column} FROM {table} ep
            LEFT JOIN nodes n ON n.id=ep.node_id LEFT JOIN processes p ON p.pid=ep.pid
            WHERE {scope} AND ep.created_ns<=?''', [*values, filters.end])}
    if selected:
        names &= set(selected)
    if grouping == 'service' and filters.exclude_default_services:
        names = {name for name in names if not is_default_service(name)}
    if filters.none:
        names.clear()
    names = {name for name in names if not action_channel(name, grouping)}
    return [{'id': name, 'label': name} for name in sorted(names)]


def density(db, source, where, params, group, a, b, start, end, lanes, bins):
    step = max(1, (end-start+1)/bins)
    query = f'''WITH matched AS MATERIALIZED (
        SELECT {group} g,CAST((max({a},?)-?)/? AS INTEGER) a,
        CAST((min({b},?)-?)/? AS INTEGER)+1 b FROM {source} WHERE {where})
        SELECT g,a bin,count(*) delta FROM matched GROUP BY g,a
        UNION ALL SELECT g,b bin,-count(*) delta FROM matched GROUP BY g,b'''
    changes = {lane['id']: [0]*(bins+2) for lane in lanes}
    for row in db.execute(query, [start,start,step,end,start,step,*params]):
        changes[row['g']][max(0,min(bins+1,row['bin']))] += row['delta']
    result = []
    for lane, deltas in changes.items():
        active = 0
        for index in range(bins):
            active += deltas[index]
            if active:
                result.append({'lane': lane, 'bin': index, 'count': active})
    return result, step


def topic_points(db, filters, start, end, lanes, budget, bins):
    source, where, params = publication_scope(filters)
    where += f' AND pub.topic IN ({",".join("?" for _ in lanes)}) AND pub.timestamp_ns>=? AND pub.timestamp_ns<=?'
    params += [*[lane['id'] for lane in lanes], start, end]
    source += ' LEFT JOIN nodes publisher_node ON publisher_node.id=pub.node_id '
    rows = db.execute(f'''SELECT pub.id,pub.execution_id,pub.pid,pub.tid,pub.timestamp_ns,
        pub.topic lane,pub.topic,publisher_node.name node_name FROM {source} WHERE {where}
        ORDER BY pub.timestamp_ns,pub.id LIMIT ?''', [*params,budget+1]).fetchall()
    if len(rows) <= budget:
        return [publication_dict(row) for row in rows], 'events'
    per_lane = max(1,min(bins,budget//len(lanes)))
    step = max(1,(end-start+1)/per_lane)
    rows = db.execute(f'''SELECT pub.topic lane,pub.topic,min(pub.timestamp_ns) timestamp_ns,
        max(pub.timestamp_ns) end_ns,count(*) count,
        CAST((pub.timestamp_ns-?)/? AS INTEGER) bin
        FROM {source} WHERE {where} GROUP BY pub.topic,bin''', [start,step,*params])
    result = []
    for row in rows:
        value = dict(row)
        value['timestamp_ns'], value['end_ns'] = str(value['timestamp_ns']),str(value['end_ns'])
        result.append(value)
    return result, 'density'


def communication_timeline(db, filters, grouping, all_lanes, start, end, lane_offset,
                           lane_limit, budget, bins, fields, encode):
    start, end = max(start,filters.start), min(end,filters.end)
    lanes = all_lanes[lane_offset:lane_offset+lane_limit]
    result = {'mode':'executions','lanes':lanes,'total_lanes':len(all_lanes),'records':[],
              'publications':[],'publish_markers_mode':'events'}
    if not lanes or end < start:
        return result
    where, params = filters.sql(include_time=False)
    source = 'intervals r CROSS JOIN ' + JOIN
    if grouping == 'topic':
        source += ' JOIN execution_topics et ON et.execution_id=e.id '
        group = 'et.topic'
        extra = ',et.subscription topic_subscription,et.publish_count topic_publish_count'
    else:
        group, extra = 'c.service', ''
        where += " AND c.type='service'"
    where += f''' AND {group} IN ({','.join('?' for _ in lanes)})
        AND e.start_ns<=? AND e.end_ns>=? AND r.start_s<=? AND r.end_s>=?'''
    params += [*[lane['id'] for lane in lanes],end,start,end/1e9,start/1e9]
    where += ' AND r.id=e.id'
    rows = db.execute(f'SELECT {fields},{group} lane{extra} FROM {source} WHERE {where} LIMIT ?',
                      [*params,budget+1]).fetchall()
    if len(rows) <= budget:
        records = [encode(row) for row in sorted(rows,key=lambda row:(row['start_ns'],row['id']))]
        result['publish_markers_mode'] = add_publish_markers(db,records,start,end,
            topics=tuple(lane['id'] for lane in lanes) if grouping=='topic' else filters.topics,
            topic_lanes=grouping=='topic',count_start=filters.start,count_end=filters.end,filters=filters)
        result['records'] = records
    else:
        cells, step = density(db,source,where,params,group,'e.start_ns','e.end_ns',start,end,lanes,bins)
        result.update(mode='density',density=cells,bins=bins,start_ns=str(start),step_ns=step,
                      message='Zoom in for individual callback executions.')
    if grouping == 'topic':
        result['publications'],result['publications_mode'] = topic_points(db,filters,start,end,lanes,budget,bins)
    return result
