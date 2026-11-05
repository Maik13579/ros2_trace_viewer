"""Read-only query layer with bounded responses and spatial interval indexing."""
from contextlib import contextmanager
from dataclasses import replace
from functools import lru_cache
import json
from pathlib import Path
import sqlite3

from .filters import JOIN
from .statistics import charts, statistics
from .publications import add_publish_markers, get_publication, list_publications, count_publications
from .topics import topic_data
from .services import service_data
from .actions import action_data
from .action_timeline import action_timeline, get_receive
from .communication_timeline import communication_lanes, communication_timeline
from .service_names import is_default_service
from .action_names import action_channel

GROUPS = {
    'process': ('CAST(e.pid AS TEXT)', "coalesce(p.name,'Unknown process')||' ['||e.pid||']'"),
    'node': ("coalesce(c.node_id,'unknown:'||e.pid)", "coalesce(n.name,'Unknown node')||' ['||e.pid||']'"),
    'thread': ("e.pid||':'||e.tid", "'Thread '||e.tid||' ['||e.pid||']'"),
    'cpu': ('CAST(s.cpu AS TEXT)', "'CPU '||s.cpu"),
}
EXECUTION_FIELDS = '''e.*, c.pointer callback_identity,c.symbol callback_symbol,
    c.type callback_type,c.topic callback_topic,c.service callback_service,c.timer_period_ns,
    c.node_id, n.name node_name,p.name process_name'''


def execution_dict(row):
    result = dict(row)
    for key in ('start_ns', 'end_ns', 'duration_ns', 'segment_start_ns', 'segment_end_ns', 'timer_period_ns'):
        if key in result and result[key] is not None:
            result[key] = str(result[key])
    return result


class ExactSum:
    """SQLite aggregate with arbitrary-precision nonnegative integer totals."""
    def __init__(self):
        self.value = 0

    def step(self, value):
        self.value += int(value)

    def finalize(self):
        return str(self.value)


class Repository:
    def __init__(self, database):
        self.path = Path(database).resolve()
        with self.connect() as db:
            self.info = {r[0]: json.loads(r[1]) for r in db.execute('SELECT * FROM info')}

    @contextmanager
    def connect(self):
        db = sqlite3.connect(f'{self.path.as_uri()}?mode=ro', uri=True)
        db.row_factory = sqlite3.Row
        db.create_aggregate('sum_ns', 1, ExactSum)
        db.execute('PRAGMA query_only=ON')
        db.execute('PRAGMA temp_store=FILE')
        try:
            yield db
        finally:
            db.close()

    def catalog(self, kind, search='', offset=0, limit=200, pid=None, node=None):
        """Paged metadata; even the hierarchy never downloads all executions."""
        sources = {
            'processes': ("processes p", 'p.pid id,p.name', 'p.name'),
            'nodes': ("""(SELECT id,pid,name FROM nodes UNION ALL
                SELECT 'unknown:'||c.pid,c.pid,NULL FROM callbacks c
                WHERE c.initialized=1 AND c.node_id IS NULL AND EXISTS
                (SELECT 1 FROM executions e WHERE e.callback_id=c.id) GROUP BY c.pid) n""", 'n.id,n.pid,n.name', 'n.name'),
            'callbacks': ('''callbacks c LEFT JOIN nodes n ON n.id=c.node_id''',
                'c.id,c.pid,c.node_id,c.pointer,c.type,c.symbol,n.name node_name,c.topic callback_topic,c.service callback_service',
                "coalesce(c.service,'')||coalesce(c.topic,'')||coalesce(n.name,'')||coalesce(c.symbol,'')||c.pointer"),
        }
        source, fields, text = sources[kind]
        where, params = [f"instr(lower(coalesce({text},'')),lower(?))>0"], [search]
        if kind == 'callbacks':
            where.append('c.initialized=1 AND EXISTS (SELECT 1 FROM executions ec WHERE ec.callback_id=c.id)')
        elif kind == 'processes':
            where.append('EXISTS (SELECT 1 FROM callbacks cf JOIN executions ef '
                         'ON ef.callback_id=cf.id WHERE cf.pid=p.pid AND cf.initialized=1)')
        else:
            where.append('EXISTS (SELECT 1 FROM callbacks cf JOIN executions ef '
                         "ON ef.callback_id=cf.id WHERE coalesce(cf.node_id,'unknown:'||cf.pid)=n.id AND cf.initialized=1)")
        if pid is not None:
            where.append(('n' if kind == 'nodes' else 'c') + '.pid=?')
            params.append(pid)
        if node is not None and kind == 'callbacks':
            where.append("coalesce(c.node_id,'unknown:'||c.pid)=?")
            params.append(node)
        condition = ' AND '.join(where)
        with self.connect() as db:
            count = db.execute(f'SELECT count(*) FROM {source} WHERE {condition}', params).fetchone()[0]
            rows = [dict(r) for r in db.execute(f'SELECT {fields} FROM {source} '
                f'WHERE {condition} ORDER BY 1 LIMIT ? OFFSET ?', [*params, limit, offset])]
        return {'items': rows, 'total': count, 'offset': offset, 'limit': limit}

    def dimensions(self):
        with self.connect() as db:
            services = [r[0] for r in db.execute('SELECT DISTINCT name FROM service_endpoints ORDER BY name')
                        if not action_channel(r[0], 'service')]
            return {
                'threads': [dict(r) for r in db.execute(
                    'SELECT DISTINCT pid,tid FROM executions ORDER BY pid,tid')],
                'cpus': [r[0] for r in db.execute('SELECT DISTINCT cpu FROM cpu_segments ORDER BY cpu')],
                'topics': [r[0] for r in db.execute('SELECT DISTINCT topic FROM endpoints ORDER BY topic')
                           if not action_channel(r[0], 'topic')],
                'services': services,
                'actions': [r[0] for r in db.execute('SELECT DISTINCT action FROM action_channels ORDER BY action')],
                'default_services': [name for name in services if is_default_service(name)],
                'types': [r[0] for r in db.execute('SELECT DISTINCT type FROM callbacks ORDER BY type')],
            }

    def execution(self, execution_id, filters=None):
        with self.connect() as db:
            row = db.execute(f'SELECT {EXECUTION_FIELDS} FROM {JOIN} WHERE e.id=?',
                             (execution_id,)).fetchone()
            if row is None:
                return None
            result = execution_dict(row)
            result['publish_count'] = count_publications(db,execution_id,filters)
            result['cpu_intervals'] = [execution_dict(r) for r in db.execute(
                'SELECT cpu,start_ns,end_ns FROM cpu_segments WHERE execution_id=? ORDER BY start_ns',
                (execution_id,))]
            return result

    @lru_cache(maxsize=8)
    def services(self, filters, offset=0, limit=50, service=None):
        with self.connect() as db:
            return service_data(db,filters,int(self.info['duration_ns']),offset,limit,service)

    @lru_cache(maxsize=8)
    def _actions(self, filters):
        with self.connect() as db:
            return action_data(db, filters, int(self.info['duration_ns']))

    def actions(self, filters, offset=0, limit=50, action=None):
        """Paged inferred action groups; activity always uses the applied filters."""
        items = self._actions(filters)
        if action is not None:
            items = [item for item in items if item['action'] == action]
        return {'items': items[offset:offset+limit], 'total': len(items),
                'offset': offset, 'limit': limit, 'transport_only': True,
                'association': 'Inferred from standard /_action/ endpoint names. '
                    'Recorded peers are declarations, not per-goal connections.'}

    @lru_cache(maxsize=32)
    def communication_lanes(self, filters, grouping):
        with self.connect() as db:
            return communication_lanes(db,filters,grouping)

    @lru_cache(maxsize=8)
    def topics(self, filters, search='', offset=0, limit=50, topic=None):
        with self.connect() as db:
            return topic_data(db, filters, int(self.info['duration_ns']), search, offset, limit, topic)

    def publication(self, publication_id, filters=None):
        with self.connect() as db:
            result = get_publication(db, publication_id, filters)
        if result is not None:
            result['execution'] = self.execution(result['execution_id'],filters) if result['execution_id'] is not None else None
        return result

    def receive(self, receive_id, filters):
        with self.connect() as db:
            return get_receive(db, receive_id, filters)

    def publications(self, execution_id, offset=0, limit=20, filters=None):
        with self.connect() as db:
            return list_publications(db, execution_id, offset, limit, filters)

    def locate(self, filters):
        where, params = filters.sql()
        with self.connect() as db:
            row = db.execute(f'SELECT e.id FROM {JOIN} WHERE {where} '
                             'ORDER BY e.start_ns,e.id LIMIT 1', params).fetchone()
        return self.execution(row[0]) if row else None

    def adjacent(self, execution_id, direction, filters):
        current = self.execution(execution_id)
        if current is None:
            return None
        where, params = replace(filters, callbacks=(current['callback_id'],)).sql()
        operator, order = ('>', 'ASC') if direction == 'next' else ('<', 'DESC')
        with self.connect() as db:
            row = db.execute(f'''SELECT e.id FROM {JOIN} WHERE {where}
                AND (e.start_ns,e.id){operator}(?,?)
                ORDER BY e.start_ns {order},e.id {order} LIMIT 1''',
                [*params, int(current['start_ns']), execution_id]).fetchone()
        return self.execution(row[0]) if row else None

    @lru_cache(maxsize=16)
    def statistics(self, filters, level, sort, descending, offset, limit):
        with self.connect() as db:
            filters = replace(filters, end=min(filters.end, int(self.info['duration_ns'])))
            return statistics(db, filters, level, sort, descending, offset, limit)

    @lru_cache(maxsize=8)
    def timers(self, filters, sort='total_ns', descending=True, offset=0, limit=50):
        """Timer-only statistics while retaining every applied global filter."""
        excluded = bool(filters.types and 'timer' not in filters.types)
        active = replace(filters,types=('timer',),none=filters.none or excluded)
        return self.statistics(active,'callback',sort,descending,offset,limit)

    @lru_cache(maxsize=8)
    def boxplots(self, filters, sort='median_ns', descending=True, offset=0, limit=25):
        """Paged callback distributions; execution arrays never reach the browser."""
        with self.connect() as db:
            filters = replace(filters, end=min(filters.end, int(self.info['duration_ns'])))
            return statistics(db, filters, 'callback', sort, descending, offset, limit,
                              include_boxplot=True)

    @lru_cache(maxsize=8)
    def charts(self, filters):
        with self.connect() as db:
            return charts(db, filters)

    @lru_cache(maxsize=32)
    def lanes(self, filters, grouping):
        group, label = GROUPS[grouping]
        source = JOIN
        if grouping == 'cpu':
            source += ' JOIN cpu_segments s ON s.execution_id=e.id '
        where, params = filters.sql(include_time=False)
        if grouping == 'cpu' and filters.cpus:
            where += f' AND s.cpu IN ({",".join("?" for _ in filters.cpus)})'
            params.extend(filters.cpus)
        with self.connect() as db:
            return [dict(r) for r in db.execute(
                f'SELECT {group} id,{label} label FROM {source} '
                f'WHERE {where} GROUP BY {group} ORDER BY id', params)]

    @lru_cache(maxsize=16)
    def timeline(self, filters, grouping, view_start, view_end, lane_offset=0,
                 lane_limit=24, budget=10000, bins=256):
        if grouping == 'action':
            lanes = [{'id': item['action'], 'label': item['action']} for item in self._actions(filters)]
            with self.connect() as db:
                return action_timeline(db,filters,lanes,view_start,view_end,lane_offset,lane_limit,
                                       budget,bins,EXECUTION_FIELDS,execution_dict)
        if grouping in ('topic','service'):
            lanes=self.communication_lanes(filters,grouping)
            with self.connect() as db:
                return communication_timeline(db,filters,grouping,lanes,view_start,view_end,
                    lane_offset,lane_limit,budget,bins,EXECUTION_FIELDS,execution_dict)
        if grouping not in GROUPS:
            raise ValueError('Unsupported timeline grouping')
        if grouping == 'cpu' and not self.info['capabilities']['cpu_execution']:
            raise ValueError(self.info['capabilities']['cpu_reason'])
        start, end = max(view_start, filters.start), min(view_end, filters.end)
        if end < start:
            return {'mode': 'executions', 'lanes': [], 'records': [], 'total_lanes': 0}
        group, label = GROUPS[grouping]
        source = JOIN
        rtree, rid, a, b = 'intervals', 'e.id', 'e.start_ns', 'e.end_ns'
        if grouping == 'cpu':
            source += ' JOIN cpu_segments s ON s.execution_id=e.id '
            rtree, rid, a, b = 'cpu_intervals', 's.id', 's.start_ns', 's.end_ns'
        where, params = filters.sql(include_time=False)
        if grouping == 'cpu' and filters.cpus:
            where += f' AND s.cpu IN ({",".join("?" for _ in filters.cpus)})'
            params.extend(filters.cpus)
        with self.connect() as db:
            lanes = self.lanes(filters,grouping)
            total_lanes = len(lanes)
            lanes = lanes[lane_offset:lane_offset + lane_limit]
            if not lanes:
                return {'mode': 'executions', 'lanes': [], 'records': [], 'total_lanes': total_lanes}
            visible = f'{group} IN ({",".join("?" for _ in lanes)})'
            where += f' AND {visible} AND {a}<=? AND {b}>=?'
            params.extend([r['id'] for r in lanes] + [end, start])
            # R-tree uses outward-rounded floats as a candidate index only;
            # exact integer comparisons above retain nanosecond precision.
            source = f'{rtree} r CROSS JOIN {source}'
            where += f' AND r.id={rid}'
            where += ' AND r.start_s<=? AND r.end_s>=?'
            params.extend([end / 1e9, start / 1e9])
            segment = f',{a} segment_start_ns,{b} segment_end_ns' if grouping == 'cpu' else ''
            rows = db.execute(f'''SELECT {EXECUTION_FIELDS},{group} lane{segment}
                FROM {source} WHERE {where} LIMIT ?''',
                [*params, budget + 1]).fetchall()
            if len(rows) <= budget:
                records = [execution_dict(r) for r in sorted(rows, key=lambda row: (
                    row['segment_start_ns'] if grouping == 'cpu' else row['start_ns'],row['id']))]
                marker_mode = add_publish_markers(db, records, start, end, topics=filters.topics,
                    count_start=filters.start,count_end=filters.end,filters=filters)
                return {'mode': 'executions', 'lanes': lanes, 'total_lanes': total_lanes,
                        'records': records, 'publish_markers_mode': marker_mode}
            # Density keeps every interval represented: an interval contributes
            # to every bin it overlaps, not just the bin containing its start.
            step = max(1, (end - start + 1) / bins)
            query = f'''WITH matched AS MATERIALIZED (
                SELECT {group} g,CAST((max({a},?)-?)/? AS INTEGER) a,
                CAST((min({b},?)-?)/? AS INTEGER)+1 b
                FROM {source} WHERE {where})
                SELECT g,a bin,count(*) delta FROM matched GROUP BY g,a
                UNION ALL SELECT g,b bin,-count(*) delta FROM matched GROUP BY g,b'''
            deltas = {lane['id']: [0] * (bins + 2) for lane in lanes}
            for row in db.execute(query, [start, start, step, end, start, step, *params]):
                index = max(0, min(bins + 1, row['bin']))
                deltas[row['g']][index] += row['delta']
            density = []
            for lane, changes in deltas.items():
                active = 0
                for index in range(bins):
                    active += changes[index]
                    if active:
                        density.append({'lane': lane, 'bin': index, 'count': active})
            return {'mode': 'density', 'lanes': lanes, 'total_lanes': total_lanes,
                    'density': density, 'bins': bins, 'start_ns': str(start),
                    'step_ns': step, 'message': 'Each cell counts all callbacks intersecting '
                    'that time bin. Zoom in to inspect individual executions.'}
