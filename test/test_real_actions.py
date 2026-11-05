"""Validate normalization against actual Jazzy CTF, not synthetic event fixtures."""
from collections import Counter
import os
from pathlib import Path

import pytest

from ros2_trace_viewer.trace.action_names import action_channel
from ros2_trace_viewer.trace.filters import Filters
from ros2_trace_viewer.trace.loader import load_trace, read_events
from ros2_trace_viewer.trace.repository import Repository


@pytest.mark.integration
def test_genuine_jazzy_action_transport(tmp_path):
    configured = os.environ.get('ROS2_TRACE_VIEWER_TEST_ACTION_TRACE')
    if not configured:
        pytest.skip('Set ROS2_TRACE_VIEWER_TEST_ACTION_TRACE to the recorded C++ action demo')
    source = Path(configured).resolve()
    def fingerprint():
        return {str(p.relative_to(source)): (p.stat().st_size, p.stat().st_mtime_ns)
                for p in source.rglob('*') if p.is_file()}
    before = fingerprint()
    publishers, subscriptions = {}, {}
    publishes, receives = Counter(), Counter()
    roles = set()
    # Independent raw RCL publish and successful RMW take counters. Layer
    # normalization must produce one observed publish, not three per message.
    for record in read_events(source):
        name, pid = record['_name'], record.get('vpid')
        if name == 'ros2:rcl_publisher_init':
            topic = record['topic_name']
            publishers[pid, record['publisher_handle']] = topic
            parsed = action_channel(topic, 'topic')
        elif name == 'ros2:rcl_subscription_init':
            topic = record['topic_name']
            subscriptions[pid, record['rmw_subscription_handle']] = topic
            parsed = action_channel(topic, 'topic')
        elif name in ('ros2:rcl_service_init', 'ros2:rcl_client_init'):
            parsed = action_channel(record['service_name'], 'service')
        else:
            parsed = None
        if parsed: roles.add(parsed)
        if name == 'ros2:rcl_publish':
            publishes[publishers.get((pid, record['publisher_handle']))] += 1
        elif name == 'ros2:rmw_take' and record.get('taken'):
            receives[subscriptions.get((pid, record['rmw_subscription_handle']))] += 1
    assert len(roles) == 5
    with load_trace(source, progress=lambda _: None, work_dir=tmp_path) as database:
        repo = Repository(database)
        assert repo.info['action_count'] == 1
        result = repo.actions(Filters())
        row = result['items'][0]
        assert len(row['channels']) == 5
        assert row['goal_count'] is row['action_type'] is None
        assert row['goal_timing_available'] is False
        timeline = repo.timeline(Filters(),'action',0,int(repo.info['duration_ns']))
        assert timeline['records'] == []
        topic_names = [base+'/_action/'+role for base,role in roles if role in ('feedback','status')]
        assert len(timeline['publications']) == sum(publishes[name] for name in topic_names)
        assert len(timeline['receives']) == sum(receives[name] for name in topic_names)
        assert all('/_action/' not in name for name in repo.dimensions()['topics'])
        assert all('/_action/' not in name for name in repo.dimensions()['services'])
        for channel in row['channels']:
            if channel['transport'] == 'topic':
                topic = channel['topic']
                assert channel['publish_count'] == publishes[topic] > 0
                assert channel['receive_count'] == receives[topic] > 0
                assert channel['publisher_count'] == 1 and channel['subscription_count'] == 2
            else:
                assert channel['server_count'] == 1 and channel['client_count'] == 2
            # This is an observation about this standard C++ waitable demo,
            # not an assumption that every ROS distribution lacks such spans.
            assert channel['execution_count'] == 0
        print({'action': row['action'], 'channels': [
            {k: value for k, value in item.items() if k in (
                'role','publish_count','receive_count','execution_count','server_count','client_count')}
            for item in row['channels']]})
    assert fingerprint() == before, 'The recorded trace must remain unchanged'
