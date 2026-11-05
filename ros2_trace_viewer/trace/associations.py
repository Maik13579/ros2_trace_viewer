"""Process-scoped metadata adapter for tracetools_analysis initialization handlers.

The upstream handler interprets ROS fields. This small data-model sink retains
only current object metadata, never a list of all trace events. Callback ownership
is resolved through explicit handle links, never through a node's creation thread.
"""
from tracetools_analysis.processor import EventMetadata
from tracetools_analysis.processor.ros2 import Ros2Handler


EVENTS = {
    'rcl_node_init', 'rcl_subscription_init', 'rclcpp_subscription_init',
    'rclcpp_subscription_callback_added', 'rcl_service_init',
    'rclcpp_service_callback_added', 'rcl_timer_init',
    'rclcpp_timer_callback_added', 'rclcpp_timer_link_node',
    'rclcpp_callback_register', 'rcl_publisher_init', 'rcl_client_init',
}


class MetadataModel:
    """Subset of Ros2DataModel's public add_* interface used for ownership."""

    def __init__(self, pid, connection):
        self.pid, self.db = pid, connection
        self.nodes, self.entities, self.subscriptions = {}, {}, {}
        self.references, self.symbols, self.kinds = {}, {}, {}
        self.node_versions = {}
        self.subscription_info, self.publishers, self.rmw_publishers = {}, {}, {}
        self.rmw_subscriptions, self.endpoint_versions = {}, {}
        self.service_info = {}
        self.timer_periods = {}

    def add_node(self, handle, timestamp, tid, rmw_handle, name, namespace):
        version = self.node_versions.get(handle, 0) + 1
        self.node_versions[handle] = version
        node_id = f'{self.pid}:{handle:x}:{version}'
        full_name = '/' + '/'.join(x for x in [namespace.strip('/'), name] if x)
        self.nodes[handle] = node_id
        self.db.execute('INSERT INTO nodes VALUES (?, ?, ?)',
                        (node_id, self.pid, full_name))

    def add_rcl_subscription(self, handle, timestamp, node, rmw, topic, depth):
        self.entities[('subscription', handle)] = self.nodes.get(node)
        info = self._endpoint('subscription', handle, timestamp, node, rmw, topic, depth)
        self.subscription_info[handle] = info
        self.rmw_subscriptions[rmw] = info

    def _endpoint(self, kind, handle, timestamp, node, rmw, name, depth):
        key = (kind, handle)
        version = self.endpoint_versions.get(key, 0) + 1
        self.endpoint_versions[key] = version
        endpoint = {'id': f'{self.pid}:{kind}:{handle:x}:{version}',
                    'node_id': self.nodes.get(node), 'name': name}
        table = 'service_endpoints' if kind in ('server', 'client') else 'endpoints'
        self.db.execute(f'INSERT INTO {table} VALUES (?,?,?,?,?,?,?,?,?)',
                        (endpoint['id'], self.pid, kind, endpoint['node_id'], name,
                         hex(handle), hex(rmw), depth, timestamp))
        return endpoint

    def add_rcl_publisher(self, handle, timestamp, node, rmw, topic, depth):
        info = self._endpoint('publisher', handle, timestamp, node, rmw, topic, depth)
        self.publishers[handle] = info
        self.rmw_publishers[rmw] = info

    def add_rclcpp_subscription(self, pointer, timestamp, handle):
        self.subscriptions[pointer] = handle

    def add_service(self, handle, timestamp, node, rmw, name):
        self.entities[('service', handle)] = self.nodes.get(node)
        self.service_info[handle] = self._endpoint('server', handle, timestamp, node, rmw, name, None)

    def add_client(self, handle, timestamp, node, rmw, name):
        self._endpoint('client', handle, timestamp, node, rmw, name, None)

    def add_timer(self, handle, timestamp, period, tid):
        self.entities[('timer', handle)] = None
        self.timer_periods[handle] = int(period) if period >= 0 else None

    def add_timer_node_link(self, handle, timestamp, node):
        self.entities[('timer', handle)] = self.nodes.get(node)

    def add_callback_object(self, reference, timestamp, callback):
        self.references[callback] = reference
        self.kinds[callback] = self.current_kind

    def add_callback_symbol(self, callback, timestamp, symbol):
        self.symbols[callback] = symbol

    def resolve(self, callback):
        kind = self.kinds.get(callback, 'unknown')
        reference = self.references.get(callback)
        if kind == 'subscription':
            reference = self.subscriptions.get(reference)
        node = self.entities.get((kind, reference))
        return node, kind, self.symbols.get(callback)

    def callback_topic(self, callback):
        if self.kinds.get(callback) != 'subscription':
            return None
        handle = self.subscriptions.get(self.references.get(callback))
        return self.subscription_info.get(handle, {}).get('name')

    def callback_service(self, callback):
        if self.kinds.get(callback) != 'service':
            return None
        return self.service_info.get(self.references.get(callback), {}).get('name')

    def callback_period(self, callback):
        """The period observed at timer initialization, never an inferred rate."""
        if self.kinds.get(callback) != 'timer':
            return None
        return self.timer_periods.get(self.references.get(callback))


class Associations:
    """One upstream ROS event handler per PID, backed by a bounded metadata sink."""

    def __init__(self, db):
        self.db, self.handlers = db, {}

    def model(self, pid):
        if pid not in self.handlers:
            handler = Ros2Handler()
            # Ros2Handler exposes data read-only; replacing its storage lets the
            # existing event interpreters feed our streaming model instead.
            handler._data_model = MetadataModel(pid, self.db)
            self.handlers[pid] = handler
        return self.handlers[pid].data

    def consume(self, event, pid):
        short = event['_name'].removeprefix('ros2:')
        if short not in EVENTS:
            return False
        model = self.model(pid)
        model.current_kind = (
            'subscription' if 'subscription' in short else
            'timer' if 'timer' in short else 'service'
        )
        metadata = EventMetadata(event['_name'], event['_timestamp'],
                                 event.get('cpu_id'), event.get('procname'),
                                 pid, event.get('vtid', event.get('tid')))
        self.handlers[pid].handler_map[event['_name']](event, metadata)
        return True
