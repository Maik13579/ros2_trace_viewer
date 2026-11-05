"""Recorded publishes within callback scopes, without cross-process message claims.

C++/RCL/RMW tracepoints on one thread for the same buffer and scope are combined
in order into one inter-process publish path. Intra-process paths stay separate.
A message pointer is a local memory address, never a globally unique message ID.
"""
from collections import OrderedDict

LAYERS = {'ros2:rclcpp_publish': 1, 'ros2:rcl_publish': 2,
          'ros2:rmw_publish': 4, 'ros2:rclcpp_intra_publish': 8}
FIELDS = ('id', 'scope_id', 'execution_id', 'pid', 'tid', 'timestamp_ns',
          'message_pointer', 'endpoint_id', 'node_id', 'topic', 'layers', 'source_timestamp')


class MessageProcessor:
    def __init__(self, db, associations, counts):
        self.db, self.associations, self.counts = db, associations, counts
        self.buffer, self.receives = [], []
        self.pending = {}
        self.next_id = self.next_scope = 1

    def scope(self):
        result = {'id': self.next_scope, 'execution_id': None, 'persisted': False}
        self.next_scope += 1
        return result

    def complete(self, scope, execution_id, thread):
        scope['execution_id'] = execution_id
        if scope['persisted']:
            self.db.execute('UPDATE publications SET execution_id=? WHERE scope_id=?',
                            (execution_id, scope['id']))
        pending = self.pending.get(thread)
        if pending:
            for key, record in list(pending.items()):
                if record['_scope'] is scope:
                    del pending[key]
            if not pending:
                self.pending.pop(thread, None)

    def clear_pending(self):
        self.pending.clear()

    def consume(self, event, pid, t, scope):
        name = event['_name']
        if name == 'ros2:rmw_take':
            # Counts are recorded take observations, not distinct global messages.
            if event.get('taken') and event.get('vtid', event.get('tid')) is not None:
                model = self.associations.model(pid)
                info = model.rmw_subscriptions.get(event.get('rmw_subscription_handle'), {})
                if not info.get('name'):
                    self.counts['receives_without_topic_metadata'] += 1
                if info.get('name'):
                    self.receives.append((info['name'], pid,
                        int(event.get('vtid', event.get('tid'))), info['node_id'], t))
            if len(self.receives) >= 8192:
                self.flush()
            return
        if name not in LAYERS:
            return
        tid, pointer = event.get('vtid', event.get('tid')), event.get('message')
        if tid is None or pointer is None:
            self.counts['missing_publish_context'] += 1
            return
        self.counts['publish_tracepoints'] += 1
        thread, layer = (pid, int(tid)), LAYERS[name]
        pending = self.pending.setdefault(thread, OrderedDict())
        key = (scope['id'] if scope else None, int(pointer))
        record = pending.get(key)
        model = self.associations.model(pid)
        info = (model.rmw_publishers.get(event.get('rmw_publisher_handle'), {})
                if layer == 4 else model.publishers.get(event.get('publisher_handle'), {}))
        # Repeated calls with a reused buffer must never collapse together.
        eligible = record is not None and not record['layers'] & layer
        if layer == 2:
            eligible = eligible and record['layers'] == 1
        elif layer == 4:
            eligible = eligible and bool(record['layers'] & 3) and not record['layers'] & 8
        else:
            eligible = False
        if eligible and info and record['endpoint_id'] and info['id'] != record['endpoint_id']:
            eligible = False
        if not eligible:
            record = dict.fromkeys(FIELDS)
            record.update(id=self.next_id, scope_id=scope['id'] if scope else None,
                          pid=pid, tid=int(tid), timestamp_ns=t,
                          message_pointer=hex(int(pointer)), layers=0,
                          _scope=scope, _persisted=False)
            self.next_id += 1
            self.counts['publishes'] += 1
            self.buffer.append(record)
        record['layers'] |= layer
        if info:
            record.update(endpoint_id=info['id'], node_id=info['node_id'], topic=info['name'])
        if layer == 4 and event.get('timestamp') is not None:
            record['source_timestamp'] = int(event['timestamp'])
        if record['_persisted']:
            self.db.execute('''UPDATE publications SET endpoint_id=?,node_id=?,topic=?,
                            layers=?,source_timestamp=? WHERE id=?''',
                            (record['endpoint_id'], record['node_id'], record['topic'],
                             record['layers'], record['source_timestamp'], record['id']))
        pending[key] = record
        pending.move_to_end(key)
        if layer in (4, 8):
            pending.pop(key, None)
        # Missing lower-layer events cannot grow pending state without bound.
        while len(pending) > 64:
            pending.popitem(last=False)
        if not pending:
            self.pending.pop(thread, None)
        if len(self.buffer) >= 8192:
            self.flush()

    def flush(self):
        if self.buffer:
            for record in self.buffer:
                scope = record['_scope']
                record['execution_id'] = scope['execution_id'] if scope else None
                record['_persisted'] = True
                if scope:
                    scope['persisted'] = True
            self.db.executemany('INSERT INTO publications VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                                (tuple(record[key] for key in FIELDS) for record in self.buffer))
            self.buffer.clear()
        if self.receives:
            self.db.executemany('INSERT INTO topic_receives VALUES (?,?,?,?,?)', self.receives)
            self.receives.clear()

    def finalize(self, origin):
        self.flush()
        self.clear_pending()
        self.db.execute('UPDATE endpoints SET created_ns=created_ns-?', (origin,))
        self.db.execute('UPDATE service_endpoints SET created_ns=created_ns-?', (origin,))
        self.db.execute('''INSERT INTO publication_counts SELECT execution_id,count(*)
                           FROM publications WHERE execution_id IS NOT NULL GROUP BY execution_id''')
        # Index each execution/topic once. A callback can both subscribe and
        # publish on the same topic, or publish repeatedly to multiple topics.
        self.db.execute('''INSERT INTO execution_topics SELECT e.id,c.topic,1,0
            FROM executions e JOIN callbacks c ON c.id=e.callback_id
            WHERE c.type='subscription' AND c.topic IS NOT NULL''')
        self.db.execute('''INSERT INTO execution_topics
            SELECT execution_id,topic,0,count(*) FROM publications
            WHERE execution_id IS NOT NULL AND topic IS NOT NULL GROUP BY execution_id,topic
            ON CONFLICT(execution_id,topic) DO UPDATE SET publish_count=excluded.publish_count''')
        self.counts['publish_in_callback'] = self.db.execute(
            'SELECT count(*) FROM publications WHERE execution_id IS NOT NULL').fetchone()[0]
        self.counts['publishes_without_topic_metadata'] = self.db.execute(
            'SELECT count(*) FROM publications WHERE topic IS NULL').fetchone()[0]
        self.counts['recorded_receives'] = self.db.execute(
            'SELECT count(*) FROM topic_receives').fetchone()[0]
