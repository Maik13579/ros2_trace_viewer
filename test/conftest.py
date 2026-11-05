"""Synthetic edge-case events; real CTF coverage is kept in test_real_trace.py."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ros2_trace_viewer.trace.models import create_database
from ros2_trace_viewer.trace.processor import Processor
from ros2_trace_viewer.trace.repository import Repository

EPOCH = 1793865350560335636


def event(name, t, pid=1, tid=11, **fields):
    return {'_name': name if ':' in name else 'ros2:' + name,
            '_timestamp': EPOCH + t, 'vpid': pid, 'vtid': tid,
            'procname': f'process_{pid}', 'cpu_id': 0, **fields}


def node(handle=100, name='alpha', pid=1, t=0):
    return event('rcl_node_init', t, pid=pid, node_handle=handle,
                 rmw_handle=handle + 1, node_name=name, namespace='/robot')


def timer(callback=500, handle=101, owner=100, pid=1, t=0, symbol='alpha::tick()'):
    return [event('rcl_timer_init', t, pid=pid, timer_handle=handle, period=10),
            event('rclcpp_timer_link_node', t, pid=pid, timer_handle=handle, node_handle=owner),
            event('rclcpp_timer_callback_added', t, pid=pid, timer_handle=handle, callback=callback),
            event('rclcpp_callback_register', t, pid=pid, callback=callback, symbol=symbol)]


def execution(start, end, callback=500, pid=1, tid=11, **fields):
    return [event('callback_start', start, pid=pid, tid=tid, callback=callback, **fields),
            event('callback_end', end, pid=pid, tid=tid, callback=callback, **fields)]


@pytest.fixture
def make_repository(tmp_path):
    def make(events):
        path = tmp_path / f'trace-{len(list(tmp_path.glob("*.sqlite")))}.sqlite'
        db = create_database(path)
        processor = Processor(db)
        for record in sorted(events, key=lambda r: r['_timestamp']):
            processor.consume(record)
        info = processor.finalize('synthetic')
        db.close()
        return Repository(path), info
    return make


def repository_events():
    """Small overlapping execution recording shared by API and browser tests."""
    events = [node(), node(200, 'beta'), node(pid=2, name='other')]
    events += timer() + timer(600, 201, 200, symbol='beta::work()') + timer(pid=2)
    events += execution(10, 30) + execution(40, 80)
    events += execution(20, 50, callback=600, tid=12) + execution(15, 25, pid=2)
    events += [event('rcl_publisher_init', 0, publisher_handle=900, node_handle=100,
                     rmw_publisher_handle=901, topic_name='/output', queue_depth=10),
               event('rcl_subscription_init', 0, subscription_handle=300, node_handle=200,
                     rmw_subscription_handle=301, topic_name='/output', queue_depth=10),
               event('rclcpp_publish', 11, message=1000),
               event('rcl_publish', 12, message=1000, publisher_handle=900),
               event('rmw_publish', 13, message=1000, rmw_publisher_handle=901, timestamp=EPOCH+11),
               event('rmw_take', 31, tid=12, message=2000, rmw_subscription_handle=301,
                     source_timestamp=EPOCH+11, taken=1),event('rcl_init', 100)]
    return events


@pytest.fixture
def repository(make_repository):
    return make_repository(repository_events())[0]
