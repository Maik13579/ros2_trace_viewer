"""Explicit application call sites, attributed only to recorded callback scopes."""
from dataclasses import replace
from .action_names import action_channel, resource_scope
from .topics import callback_scope, owner_scope
from .service_names import default_service_predicate


def call_scope(filters):
    base, params=replace(filters,search='',actions=()).sql()
    direct, values=owner_scope(filters,'call.pid','call.node_id','call.tid')
    where=f'(call.execution_id IS NOT NULL AND ({base}))'
    if not callback_scope(filters):
        where+=f' OR (call.execution_id IS NULL AND ({direct}))';params+=values
    where=f'({where}) AND call.timestamp_ns>=? AND call.timestamp_ns<=?'
    params.extend((filters.start,filters.end))
    resources,values=resource_scope(filters,'call.service','service')
    where+=f' AND ({resources})';params+=values
    if filters.exclude_default_services:
        standard,values=default_service_predicate('call.service');where+=f' AND NOT {standard}';params+=values
    if filters.search:
        where+=" AND instr(lower(call.service||' '||coalesce(n.name,'')||' '||coalesce(p.name,'')||' '||coalesce(c.symbol,'')),lower(?))>0"
        params.append(filters.search)
    source='''client_calls call LEFT JOIN executions e ON e.id=call.execution_id
        LEFT JOIN callbacks c ON c.id=e.callback_id LEFT JOIN nodes n ON n.id=call.node_id
        LEFT JOIN processes p ON p.pid=call.pid'''
    return source,where,params


def encode_call(row):
    result=dict(row)
    for key in ('timestamp_ns','wait_end_ns','end_ns','request_id'):
        if result.get(key) is not None:result[key]=str(result[key])
    parsed=action_channel(result.get('service'),'service')
    result.update(kind='action' if parsed else 'service',
                  action=parsed[0] if parsed else None,operation=parsed[1] if parsed else None)
    result['wait_for_response']=bool(result.get('wait_for_response'))
    if result.get('wait_end_ns') is not None:
        result['wait_duration_ns']=str(int(result['wait_end_ns'])-int(result['timestamp_ns']))
    return result


def get_call(db,identity,filters):
    source,where,params=call_scope(filters)
    row=db.execute(f'SELECT call.*,n.name node_name,p.name process_name FROM {source} '
                   f'WHERE ({where}) AND call.id=?',[*params,identity]).fetchone()
    return encode_call(row) if row else None


def list_calls(db,execution_id,filters,offset=0,limit=20):
    source,where,params=call_scope(filters)
    where+=' AND call.execution_id=?';params.append(execution_id)
    total=db.execute(f'SELECT count(*) FROM {source} WHERE {where}',params).fetchone()[0]
    rows=db.execute(f'SELECT call.*,n.name node_name,p.name process_name FROM {source} '
        f'WHERE {where} ORDER BY call.timestamp_ns,call.id LIMIT ? OFFSET ?',[*params,limit,offset])
    return {'items':[encode_call(row) for row in rows],'total':total,'offset':offset,'limit':limit}


def add_call_markers(db,records,start,end,filters,limit=10000):
    """Bounded callback lists; keep exact call counts and aggregate crowded bins."""
    ids=sorted({record['id'] for record in records})
    if not ids:return
    source,where,params=call_scope(filters)
    where+=f' AND call.execution_id IN ({",".join("?" for _ in ids)})'
    params+=ids
    counts=dict(db.execute(f'SELECT call.execution_id,count(*) FROM {source} WHERE {where} '
                           'GROUP BY call.execution_id',params))
    where+=' AND call.timestamp_ns>=? AND call.timestamp_ns<=?';params+=[start,end]
    rows=db.execute(f'SELECT call.*,n.name node_name,p.name process_name FROM {source} WHERE {where} '
                    'ORDER BY call.timestamp_ns,call.id LIMIT ?',[*params,limit+1]).fetchall()
    if len(rows)>limit:
        bins=max(1,min(128,limit//len(ids)));step=max(1,(end-start+1)/bins)
        rows=db.execute(f'''SELECT call.execution_id,min(call.timestamp_ns) timestamp_ns,
            max(call.timestamp_ns) end_ns,count(*) count,
            CASE WHEN count(DISTINCT call.service)=1 THEN min(call.service) END service,
            CAST((call.timestamp_ns-?)/? AS INTEGER) bin FROM {source} WHERE {where}
            GROUP BY call.execution_id,bin''',[start,step,*params]).fetchall()
    found={identity:[] for identity in ids}
    for row in rows:found[row['execution_id']].append(encode_call(row))
    for record in records:
        calls=found[record['id']]
        if filters.actions and record.get('lane') in filters.actions:
            calls=[call for call in calls if call.get('action')==record['lane']]
        record.update(call_count=counts.get(record['id'],0),calls=calls)
