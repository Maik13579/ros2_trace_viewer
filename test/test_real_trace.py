"""Integration against a genuine recording, separate from synthetic edge tests.

Set ROS2_TRACE_VIEWER_TEST_TRACE to a finished Jazzy LTTng recording. Nothing is
written inside it; the upstream pickle/converted cache is intentionally ignored.
"""
from collections import defaultdict
from pathlib import Path
import os

import pytest

from ros2_trace_viewer.trace.filters import Filters
from ros2_trace_viewer.trace.loader import load_trace, read_events
from ros2_trace_viewer.trace.repository import Repository


@pytest.mark.integration
def test_genuine_jazzy_ctf(tmp_path):
    configured = os.environ.get('ROS2_TRACE_VIEWER_TEST_TRACE')
    if not configured:
        pytest.skip('Set ROS2_TRACE_VIEWER_TEST_TRACE to test a genuine Jazzy CTF recording')
    source = Path(configured).resolve()
    before = {str(p.relative_to(source)): (p.stat().st_size, p.stat().st_mtime_ns)
              for p in source.rglob('*') if p.is_file()}
    starts = defaultdict(list)
    count = total = known_cpp_publishes = 0
    active = defaultdict(list)
    initialized = set()
    # Independent reference matcher for this integration recording. The unit
    # suite separately covers nesting, concurrency, missing events and loss.
    for record in read_events(source):
        name = record['_name']
        pid, tid, callback = record.get('vpid'), record.get('vtid'), record.get('callback')
        if name in ('ros2:rclcpp_callback_register', 'ros2:rclcpp_subscription_callback_added',
                    'ros2:rclcpp_timer_callback_added', 'ros2:rclcpp_service_callback_added'):
            initialized.add((pid, callback))
        key = (pid, tid, callback)
        if name == 'ros2:callback_start':
            scope={'start':record['_timestamp'],'known':(pid, callback) in initialized,'cpp':0}
            starts[key].append(scope);active[(pid,tid)].append(scope)
        elif name == 'ros2:callback_end' and starts[key]:
            scope=starts[key].pop()
            if scope in active[(pid,tid)]:active[(pid,tid)].remove(scope)
            if scope['known']:
                count += 1; total += record['_timestamp'] - scope['start']; known_cpp_publishes+=scope['cpp']
        elif name=='ros2:rclcpp_publish' and active[(pid,tid)]:
            active[(pid,tid)][-1]['cpp']+=1
    assert count > 0, 'The genuine recording must contain complete registered callback executions'
    with load_trace(source, progress=lambda _: None, work_dir=tmp_path) as path:
        repository = Repository(path)
        assert repository.info['known_execution_count'] == count
        result = repository.statistics(Filters(), 'process', 'total_ns', True, 0, 100)
        assert result['execution_count'] == count
        assert int(result['total_callback_ns']) == total
        assert sum(float(row['share_percent']) for row in result['rows']) == pytest.approx(100)
        with repository.connect() as db:
            assert db.execute('SELECT count(*) FROM executions WHERE end_ns-start_ns != duration_ns OR duration_ns<0').fetchone()[0] == 0
            assert db.execute("SELECT count(*) FROM publications p JOIN executions e ON e.id=p.execution_id WHERE p.pid!=e.pid OR p.tid!=e.tid OR p.timestamp_ns<e.start_ns OR p.timestamp_ns>e.end_ns").fetchone()[0]==0
            assert db.execute("SELECT count(*) FROM publications p JOIN executions e ON e.id=p.execution_id JOIN callbacks c ON c.id=e.callback_id WHERE c.initialized=1 AND (p.layers & 1)!=0").fetchone()[0]==known_cpp_publishes

        first = repository.locate(Filters())
        assert first['callback_symbol'] and first['node_name']
        topics=repository.timeline(Filters(),'topic',0,int(repository.info['duration_ns']),lane_limit=64,budget=20000)
        records=topics['records']
        assert len({(r['id'],r['lane']) for r in records})==len(records)
        for record in records:
            assert record['topic_subscription'] or record['topic_publish_count']>0
            assert all(pub['topic']==record['lane'] for pub in record['publishes'])
            assert repository.execution(record['id'])['duration_ns']==record['duration_ns']
    after = {str(p.relative_to(source)): (p.stat().st_size, p.stat().st_mtime_ns)
             for p in source.rglob('*') if p.is_file()}
    assert before == after, 'The input trace must remain read-only'
