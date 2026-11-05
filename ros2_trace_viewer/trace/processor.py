"""Single-pass callback matching with process and thread identity.

A per-thread stack permits recursion and simultaneous execution of the same
callback on different threads. An out-of-order end abandons intervening starts;
those starts cannot later form a misleading duration. Known packet loss clears
open stacks. Unmatched or context-free records are counted, never fabricated.
"""
from collections import Counter, defaultdict
import json

from .associations import Associations
from .models import INDICES
from .messages import MessageProcessor


class Processor:
    def __init__(self, db):
        self.db, self.associations = db, Associations(db)
        self.origin = self.last = None
        self.stacks = defaultdict(list)
        self.active_cpus = {}
        self.counts = Counter()
        self.callback_ids = {}
        self.callback_versions = Counter()
        self.process_names = {}
        self.buffer, self.schedule_buffer = [], []
        self.next_id = 1
        self.seen = set()
        self.messages = MessageProcessor(db, self.associations, self.counts)

    def consume(self, event):
        timestamp, name = int(event['_timestamp']), event['_name']
        if self.origin is None:
            self.origin = timestamp
        if self.last is not None and timestamp < self.last:
            raise ValueError('CTF events are not in chronological order')
        self.last = timestamp
        self.counts['events'] += 1
        self.seen.add(name)
        if event.get('_discarded', 0):
            self.counts['discarded_events'] += event['_discarded']
            self.counts['missing_end'] += sum(map(len, self.stacks.values()))
            self.stacks.clear()
            self.active_cpus.clear()
            self.messages.clear_pending()
        t = timestamp - self.origin
        if name in ('sched_switch', 'sched:sched_switch'):
            self._switch(event, t)
        if not name.startswith('ros2:'):
            return
        pid = event.get('vpid', event.get('pid'))
        if pid is None:
            self.counts['missing_process_context'] += 1
            return
        pid = int(pid)
        procname = event.get('procname')
        if pid not in self.process_names or (procname and not self.process_names[pid]):
            self.process_names[pid] = procname
            self.db.execute('INSERT OR REPLACE INTO processes VALUES (?, ?)', (pid, procname))
        try:
            if self.associations.consume(event, pid):
                return
        except (AttributeError, KeyError, TypeError, ValueError):
            self.counts['incomplete_metadata'] += 1
            return
        thread = (pid, event.get('vtid', event.get('tid')))
        active = self.stacks.get(thread)
        self.messages.consume(event, pid, t, active[-1][5] if active else None)
        if name not in ('ros2:callback_start', 'ros2:callback_end'):
            return
        tid, pointer = event.get('vtid', event.get('tid')), event.get('callback')
        if tid is None or pointer is None:
            self.counts['missing_callback_context'] += 1
            return
        stack = self.stacks[(pid, int(tid))]
        if name.endswith('callback_start'):
            callback_id = self._callback(pid, pointer)
            stack.append((pointer, callback_id, t, event.get('cpu_id'), event.get('tid'),
                          self.messages.scope()))
            return
        index = next((i for i in range(len(stack) - 1, -1, -1)
                      if stack[i][0] == pointer), None)
        if index is None:
            self.counts['missing_start'] += 1
            return
        self.counts['missing_end'] += len(stack) - index - 1
        _, callback_id, start, cpu, host_tid, scope = stack[index]
        del stack[index:]
        if not stack:
            del self.stacks[(pid, int(tid))]
        duration = t - start
        if duration < 0:
            self.counts['invalid_duration'] += 1
            return
        self.buffer.append((self.next_id, callback_id, pid, int(tid), host_tid,
                            start, t, duration, cpu, event.get('cpu_id')))
        self.messages.complete(scope, self.next_id, (pid, int(tid)))
        self.next_id += 1
        self.counts['executions'] += 1
        if len(self.buffer) >= 8192:
            self.flush()

    def _callback(self, pid, pointer):
        node, kind, symbol = self.associations.model(pid).resolve(pointer)
        model = self.associations.model(pid)
        initialized = pointer in model.references or pointer in model.symbols
        topic = model.callback_topic(pointer)
        service = model.callback_service(pointer)
        period = model.callback_period(pointer)
        metadata = (node, kind, symbol, initialized, topic, service, period)
        key = (pid, pointer)
        previous = self.callback_ids.get(key)
        if previous is not None and previous[0] == metadata:
            return previous[1]
        self.callback_versions[key] += 1
        cid = f'{pid}:{int(pointer):x}:{self.callback_versions[key]}'
        self.callback_ids[key] = (metadata, cid)
        self.db.execute('INSERT INTO callbacks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                        (cid, pid, hex(int(pointer)), node, kind, symbol, int(initialized), topic, service, period))
        return cid

    def _switch(self, event, t):
        cpu, previous, next_tid = (event.get(x) for x in ('cpu_id', 'prev_tid', 'next_tid'))
        # Linux LTTng names the host thread ID prev_tid/next_tid; some captures
        # (e.g. perf exports) use prev_pid/next_pid instead.
        previous = previous if previous is not None else event.get('prev_pid')
        next_tid = next_tid if next_tid is not None else event.get('next_pid')
        if cpu is None or previous is None or next_tid is None:
            return
        active = self.active_cpus.get(cpu)
        if active and active[0] == previous and t >= active[1]:
            self.schedule_buffer.append((int(previous), int(cpu), active[1], t))
        elif active:
            self.counts['scheduler_gaps'] += 1
        # The first switch establishes state; no unobserved interval is inferred.
        self.active_cpus[cpu] = (next_tid, t)
        self.counts['scheduler_switches'] += 1
        if len(self.schedule_buffer) >= 8192:
            self.flush()

    def flush(self):
        self.messages.flush()
        if self.buffer:
            self.db.executemany('INSERT INTO executions VALUES (?,?,?,?,?,?,?,?,?,?)', self.buffer)
            self.db.executemany('INSERT INTO intervals VALUES (?,?,?)',
                                ((r[0], r[5] / 1e9, r[6] / 1e9) for r in self.buffer))
            self.buffer.clear()
        if self.schedule_buffer:
            self.db.executemany('INSERT INTO scheduler VALUES (?,?,?,?)', self.schedule_buffer)
            self.schedule_buffer.clear()
        self.db.commit()

    def finalize(self, trace_name):
        self.counts['missing_end'] += sum(map(len, self.stacks.values()))
        self.flush()
        self.messages.finalize(self.origin or 0)
        self.db.executescript(INDICES)
        self.db.execute('''INSERT INTO cpu_segments(execution_id,cpu,start_ns,end_ns)
            SELECT e.id,s.cpu,max(e.start_ns,s.start_ns),min(e.end_ns,s.end_ns)
            FROM executions e JOIN scheduler s ON s.tid=e.host_tid
            AND e.start_ns<s.end_ns AND e.end_ns>s.start_ns''')
        self.db.execute('''INSERT INTO cpu_intervals SELECT id,start_ns/1e9,end_ns/1e9
                           FROM cpu_segments''')
        cpu_count = self.db.execute('SELECT count(*) FROM cpu_segments').fetchone()[0]
        duration = (self.last - self.origin) if self.origin is not None else 0
        info = {
            'name': trace_name, 'origin_ns': str(self.origin or 0),
            'duration_ns': str(duration), 'duration_seconds': duration / 1e9,
            'execution_count': self.counts['executions'],
            'known_execution_count': self.db.execute('SELECT count(*) FROM executions e '
                'JOIN callbacks c ON c.id=e.callback_id WHERE c.initialized=1').fetchone()[0],
            'process_count': self.db.execute('SELECT count(*) FROM processes').fetchone()[0],
            'node_count': self.db.execute('SELECT count(*) FROM nodes').fetchone()[0],
            'diagnostics': dict(self.counts), 'events': sorted(self.seen),
            'capabilities': {
                'scheduler_events': self.counts['scheduler_switches'] > 0,
                'cpu_execution': cpu_count > 0,
                'publish_markers': self.counts['publishes'] > 0,
                'cpu_reason': ('CPU lanes show only observed on-CPU intervals; gaps remain unknown.'
                    if cpu_count else 'Accurate CPU lanes need sched_switch events and ROS host '
                    'tid context in the same clock domain. Boundary CPU IDs are observations '
                    'only; wall-clock duration includes preemption, waiting and blocking.'),
            },
            'statistics_policy': 'Executions fully contained in the selected time range are '
                'aggregated using their full wall-clock duration. Boundary-crossing executions '
                'remain visible on the timeline and are counted separately. Standard deviation '
                'is population standard deviation; quantiles use linear interpolation. '
                'Overlapping durations are cumulative callback time, not CPU utilization.',
        }
        self.db.executemany('INSERT INTO info VALUES (?, ?)',
                            ((key, json.dumps(value)) for key, value in info.items()))
        self.db.commit()
        return info
