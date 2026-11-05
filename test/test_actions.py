"""Action endpoint inference, scoped transport activity, and honest unknowns."""
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from conftest import event, execution, node, timer
from test_publications import publisher
from ros2_trace_viewer.server import create_app
from ros2_trace_viewer.trace.action_names import action_channel
from ros2_trace_viewer.trace.filters import Filters
from ros2_trace_viewer.trace.loader import load_trace
from ros2_trace_viewer.trace.repository import Repository

ACTION = '/robot/one/fibonacci'


def action_events(name=ACTION, server=1, client=2):
    """Match the standard action waitable's declarations without fake handlers."""
    events = [node(pid=server, name='server'), node(pid=client, name='client')]
    for index, role in enumerate(('send_goal', 'cancel_goal', 'get_result')):
        service = name + '/_action/' + role
        events += [event('rcl_service_init', 0, pid=server, service_handle=400+index,
                         node_handle=100, rmw_service_handle=500+index, service_name=service),
                   event('rcl_client_init', 0, pid=client, client_handle=600+index,
                         node_handle=100, rmw_client_handle=700+index, service_name=service)]
    for index, role in enumerate(('feedback', 'status')):
        topic = name + '/_action/' + role
        events += [publisher(pid=server, handle=900+index, rmw=950+index, topic=topic),
                   event('rcl_subscription_init', 0, pid=client, subscription_handle=300+index,
                         node_handle=100, rmw_subscription_handle=350+index,
                         topic_name=topic, queue_depth=10)]
    events += [event('rcl_publish', 4, pid=server, message=1000, publisher_handle=900),
               event('rcl_publish', 20, pid=server, message=1001, publisher_handle=900),
               event('rcl_publish', 12, pid=server, message=1002, publisher_handle=901),
               event('rmw_take', 5, pid=client, rmw_subscription_handle=350, message=2000, taken=1),
               event('rmw_take', 21, pid=client, rmw_subscription_handle=350, message=2001, taken=1),
               event('rmw_take', 14, pid=client, rmw_subscription_handle=351, message=2002, taken=1),
               event('rcl_init', 30, pid=server)]
    return events


@pytest.mark.parametrize('name,transport,expected', [
    (ACTION+'/_action/send_goal', 'service', (ACTION, 'send_goal')),
    ('/fibonacci/_action/feedback', 'topic', ('/fibonacci', 'feedback')),
    (ACTION+'/_action/status', 'topic', (ACTION, 'status')),
    (ACTION+'/_action/status', 'service', None),
    (ACTION+'/_action/get_result', 'topic', None),
    (ACTION+'/_action/not_a_channel', 'topic', None),
    ('/_action/feedback', 'topic', None),
    ('relative/_action/status', 'topic', None),
    (ACTION+'/_action/status/extra', 'topic', None),
    ('/ordinary/status', 'topic', None),
])
def test_standard_action_names(name, transport, expected):
    assert action_channel(name, transport) == expected


def channels(row):
    return {channel['role']: channel for channel in row['channels']}


def test_transport_only_action_without_callback_spans(make_repository):
    repo, info = make_repository(action_events())
    assert info['execution_count'] == 0 and info['action_count'] == 1
    assert info['capabilities']['action_transport'] is True
    assert info['capabilities']['action_goal_timing'] is False
    result = repo.actions(Filters())
    assert result['transport_only'] and result['total'] == 1
    row = result['items'][0]
    assert row['action'] == ACTION and len(row['recorded_roles']) == 5
    assert row['server_nodes'] == ['/robot/server'] and row['client_nodes'] == ['/robot/client']
    assert row['action_type'] is None and row['goal_count'] is None
    assert row['goal_ids_available'] is row['goal_timing_available'] is False
    assert row['first_ns'] == '4' and row['last_ns'] == '21'
    data = channels(row)
    assert data['feedback']['publish_count'] == data['feedback']['receive_count'] == 2
    assert data['status']['publish_count'] == data['status']['receive_count'] == 1
    assert data['send_goal']['server_count'] == data['send_goal']['client_count'] == 1
    assert all(item['execution_count'] == 0 for item in data.values())


def test_action_filters_preserve_resource_independence(make_repository):
    repo = make_repository(action_events())[0]
    f = Filters(topics=('__none__',), services=('__none__',), actions=(ACTION,))
    row = repo.actions(f)['items'][0]
    assert len(channels(row)) == 5
    assert row['recorded_roles'] == ['cancel_goal', 'feedback', 'get_result', 'send_goal', 'status']
    assert repo.actions(replace(f, actions=('__none__',)))['total'] == 0
    row = repo.actions(Filters(topics=('__none__',)))['items'][0]
    assert len(channels(row)) == 5
    row = repo.actions(Filters(services=('__none__',)))['items'][0]
    assert len(channels(row)) == 5
    assert repo.actions(Filters(none=True))['items'] == []
    assert repo.actions(Filters(search='not present'))['items'] == []
    assert repo.actions(Filters(exclude_default_services=True))['total'] == 1


def test_action_activity_obeys_time_and_owner_filters(make_repository):
    repo = make_repository(action_events())[0]
    row = repo.actions(Filters(start=6, end=18))['items'][0]
    assert channels(row)['feedback']['publish_count'] == 0
    assert channels(row)['status']['publish_count'] == 1
    assert row['first_ns'] == '12' and row['last_ns'] == '14'
    row = repo.actions(Filters(processes=(2,)))['items'][0]
    assert channels(row)['feedback']['publish_count'] == 0
    assert channels(row)['feedback']['receive_count'] == 2
    assert repo.actions(Filters(exclude_processes=(1,2)))['items'] == []
    assert repo.actions(Filters(search='fibonacci'))['total'] == 1


def test_callback_filters_do_not_invent_action_handler_durations(make_repository):
    repo = make_repository([*action_events(), *timer(), *execution(1, 10)])[0]
    row = repo.actions(Filters(types=('timer',)))['items'][0]
    assert list(channels(row)) == ['feedback']
    assert channels(row)['feedback']['publish_count'] == 1
    assert channels(row)['feedback']['receive_count'] is None
    assert row['goal_timing_available'] is False
    assert repo.actions(Filters(types=('service',)))['items'] == []


def test_namespaces_partial_evidence_and_metadata_time(make_repository):
    partial = publisher(pid=3, topic='/robot/two/fibonacci/_action/feedback')
    partial['_timestamp'] += 10
    repo = make_repository([*action_events(), node(pid=3), partial])[0]
    assert repo.info['action_count'] == 2
    assert repo.actions(Filters(end=5))['total'] == 1
    result = repo.actions(Filters(), offset=1, limit=1)
    row = result['items'][0]
    assert result['total'] == 2 and row['action'] == '/robot/two/fibonacci'
    assert row['recorded_roles'] == ['feedback']
    assert row['goal_count'] is None
    assert repo.actions(Filters(), action=ACTION)['total'] == 1
    assert repo.actions(Filters(), action='/absent')['items'] == []


def test_action_api_is_bounded_and_read_only(make_repository):
    repo = make_repository(action_events())[0]
    with TestClient(create_app(repo.path)) as client:
        result = client.get('/api/actions').json()
        assert result['total'] == 1
        assert client.get('/api/actions', params={'action': ACTION}).json()['items'][0]['action'] == ACTION
        assert client.get('/api/actions?limit=101').status_code == 422
        assert client.get('/api/actions?offset=-1').status_code == 422
        assert client.get('/api/actions?start_time=bad').status_code == 422
        assert client.get('/api/actions?none=1').json()['items'] == []
        assert client.post('/api/actions').status_code == 405
        assert client.get('/api/trace/info').json()['action_count'] == 1


def test_loader_accepts_action_transport_without_known_callbacks(tmp_path, monkeypatch):
    source = tmp_path/'source'; source.mkdir(); (source/'metadata').write_text('CTF fixture')
    monkeypatch.setattr('ros2_trace_viewer.trace.loader.read_events',
                        lambda _: iter(sorted(action_events(), key=lambda item: item['_timestamp'])))
    with load_trace(source, progress=lambda _: None, work_dir=tmp_path) as database:
        repo = Repository(database)
        assert repo.info['known_execution_count'] == 0
        assert repo.actions(Filters())['total'] == 1


def test_no_actions_in_ordinary_trace(repository):
    assert repository.actions(Filters())['items'] == []
    assert repository.info['capabilities']['action_transport'] is False
