"""Validated shared query filters; no user input is interpolated into SQL."""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


MAX_NS = 2**63 - 1
LIST_FIELDS = {'processes': 'e.pid', 'nodes': "coalesce(c.node_id, 'unknown:'||e.pid)",
               'callbacks': 'e.callback_id', 'types': 'c.type', 'threads': 'e.tid', 'services': 'c.service'}


def to_ns(value):
    """Parse API seconds exactly; API nanosecond values are serialized as strings."""
    try:
        parsed = Decimal(str(value)) * 1_000_000_000
        if not parsed.is_finite() or parsed < 0 or parsed > MAX_NS:
            raise ValueError('Time and duration values must be finite, nonnegative seconds')
        return int(parsed)
    except (InvalidOperation, TypeError) as error:
        raise ValueError('Invalid time or duration; expected seconds') from error


@dataclass(frozen=True)
class Filters:
    start: int = 0
    end: int = MAX_NS
    min_duration: int = 0
    max_duration: int = MAX_NS
    processes: tuple = ()
    nodes: tuple = ()
    callbacks: tuple = ()
    exclude_processes: tuple = ()
    exclude_nodes: tuple = ()
    exclude_callbacks: tuple = ()
    types: tuple = ()
    threads: tuple = ()
    cpus: tuple = ()
    search: str = ''
    none: bool = False
    known_only: bool = True
    topics: tuple = ()
    services: tuple = ()
    include_selection: bool = False
    include_processes: tuple = ()
    include_nodes: tuple = ()
    include_callbacks: tuple = ()

    @classmethod
    def parse(cls, query):
        values = {}
        for key in (*LIST_FIELDS, 'topics', 'cpus', 'exclude_processes', 'exclude_nodes', 'exclude_callbacks', 'include_processes', 'include_nodes', 'include_callbacks'):
            raw = query.get(key, '')
            if len(raw) > 24000:
                raise ValueError('Filter selection is too large')
            items = tuple(x for x in raw.split(',') if x)
            if len(items) > 1000:
                raise ValueError('At most 1000 selections per filter')
            if key in ('processes', 'exclude_processes', 'include_processes', 'threads', 'cpus'):
                try:
                    items = tuple(int(x) for x in items)
                    if any(abs(x) > MAX_NS for x in items):
                        raise ValueError('ID exceeds the supported integer range')
                except ValueError as error:
                    raise ValueError(f'{key} must contain integer IDs') from error
            values[key] = items
        for key, param in [('start', 'start_time'), ('end', 'end_time'),
                           ('min_duration', 'min_duration'), ('max_duration', 'max_duration')]:
            if query.get(param, '') != '':
                values[key] = to_ns(query[param])
        values['search'] = query.get('search', '')[:500]
        values['include_selection'] = query.get('include_selection') == '1'
        values['none'] = query.get('none') == '1'
        result = cls(**values)
        if result.end < result.start or result.max_duration < result.min_duration:
            raise ValueError('Range end must be at least its start')
        return result

    def sql(self, boundary='overlap', include_time=True):
        clauses, params = ['e.duration_ns>=?', 'e.duration_ns<=?'], [
            self.min_duration, self.max_duration]
        if self.known_only:
            clauses.append('c.initialized=1')
        if self.none:
            clauses.append('0')
        if include_time:
            if boundary == 'contained':
                clauses += ['e.start_ns>=?', 'e.end_ns<=?']
            else:
                clauses += ['e.end_ns>=?', 'e.start_ns<=?']
            params += [self.start, self.end]
        for key, column in LIST_FIELDS.items():
            values = getattr(self, key)
            if values:
                clauses.append(f'{column} IN ({",".join("?" for _ in values)})')
                params.extend(values)
        for key in ('processes', 'nodes', 'callbacks'):
            values = getattr(self, 'exclude_' + key)
            if values:
                clauses.append(f'{LIST_FIELDS[key]} NOT IN ({",".join("?" for _ in values)})')
                params.extend(values)
        if self.include_selection:
            selected = []
            for key in ('processes', 'nodes', 'callbacks'):
                values = getattr(self, 'include_' + key)
                if values:
                    selected.append(f'{LIST_FIELDS[key]} IN ({",".join("?" for _ in values)})')
                    params.extend(values)
            clauses.append('(' + ' OR '.join(selected) + ')' if selected else '0')
        if self.topics:
            marks = ','.join('?' for _ in self.topics)
            clauses.append(f'(c.topic IN ({marks}) OR EXISTS '
                           f'(SELECT 1 FROM publications ft WHERE ft.execution_id=e.id AND ft.topic IN ({marks})))')
            params.extend([*self.topics, *self.topics])
        if self.cpus:
            clauses.append('EXISTS (SELECT 1 FROM cpu_segments fs WHERE fs.execution_id=e.id '
                           f'AND fs.cpu IN ({",".join("?" for _ in self.cpus)}))')
            params.extend(self.cpus)
        if self.search:
            clauses.append("instr(lower(coalesce(c.service,'')||' '||coalesce(c.topic,'')||' '||coalesce(c.symbol,'')||' '||coalesce(n.name,'unknown')"
                           "||' '||coalesce(p.name,'')||' '||c.pointer||' '||e.pid),lower(?))>0")
            params.append(self.search)
        return ' AND '.join(clauses), params


JOIN = ''' executions e JOIN callbacks c ON c.id=e.callback_id
           LEFT JOIN nodes n ON n.id=c.node_id LEFT JOIN processes p ON p.pid=e.pid '''
