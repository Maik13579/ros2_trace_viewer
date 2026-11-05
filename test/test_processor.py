import pytest

from conftest import EPOCH, event, execution, node, timer
from ros2_trace_viewer.trace.filters import Filters


def rows(repo):
    with repo.connect() as db:
        return [dict(row) for row in db.execute('SELECT e.*,c.node_id,c.initialized,c.type '
            'FROM executions e JOIN callbacks c ON c.id=e.callback_id ORDER BY e.start_ns')]


def test_repeated_and_cross_process_matching(repository):
    data = rows(repository)
    assert len(data) == 4
    assert [row['duration_ns'] for row in data] == [20, 10, 30, 40]
    assert data[0]['callback_id'] == data[3]['callback_id']
    assert data[0]['callback_id'] != data[1]['callback_id']
    assert int(repository.info['origin_ns']) == EPOCH


def test_multiple_nodes_in_one_creation_thread(repository):
    data = rows(repository)
    assert data[0]['node_id'] != data[2]['node_id']
    assert data[0]['pid'] == data[2]['pid']
    assert repository.execution(data[0]['id'])['node_name'] == '/robot/alpha'
    assert repository.execution(data[2]['id'])['node_name'] == '/robot/beta'


def test_parallel_same_callback_and_recursion(make_repository):
    events = [node(), *timer(), *execution(10, 40), *execution(20, 30, tid=12),
              *execution(50, 90), *execution(60, 70)]
    repo, _ = make_repository(events)
    assert [row['duration_ns'] for row in rows(repo)] == [30, 10, 40, 10]


def test_unmatched_ends_and_starts(make_repository):
    repo, info = make_repository([node(), *timer(), event('callback_end', 1, callback=500),
        event('callback_start', 2, callback=500), event('callback_start', 3, callback=600),
        event('callback_end', 4, callback=500), event('callback_end', 5, callback=600),
        event('callback_start', 6, callback=500)])
    assert [r['duration_ns'] for r in rows(repo)] == [2]
    assert info['diagnostics']['missing_start'] == 2
    assert info['diagnostics']['missing_end'] == 2


def test_known_loss_does_not_create_duration(make_repository):
    repo, info = make_repository([node(), *timer(), event('callback_start', 1, callback=500),
        event('rcl_init', 2, _discarded=4), event('callback_end', 3, callback=500)])
    assert rows(repo) == []
    assert info['diagnostics']['discarded_events'] == 4
    assert info['diagnostics']['missing_end'] == 1
    assert info['diagnostics']['missing_start'] == 1


def test_missing_process_and_thread_are_not_invented(make_repository):
    repo, info = make_repository([node(), *timer(),
        event('callback_start', 1, pid=None, callback=500),
        event('callback_end', 2, pid=None, callback=500),
        event('callback_start', 3, tid=None, callback=500),
        event('callback_end', 4, tid=None, callback=500)])
    assert rows(repo) == []
    assert info['diagnostics']['missing_process_context'] == 2
    assert info['diagnostics']['missing_callback_context'] == 2


def test_unknown_callback_is_stored_but_hidden(make_repository):
    repo, info = make_repository([node(), *execution(1, 2),
        event('rclcpp_callback_register', 3, callback=500, symbol='known()'),
        *execution(4, 5)])
    assert info['execution_count'] == 2 and info['known_execution_count'] == 1
    data = rows(repo)
    assert [r['initialized'] for r in data] == [0, 1]
    assert all(r['node_id'] is None for r in data)
    assert len(repo.timeline(Filters(), 'node', 0, 10)['records']) == 1
    assert len(repo.timeline(Filters(known_only=False), 'node', 0, 10)['records']) == 2
    assert repo.catalog('callbacks')['total'] == 1


def test_subscription_and_service_ownership(make_repository):
    events = [node(), node(200, 'beta'),
        event('rcl_subscription_init', 0, subscription_handle=1, node_handle=100,
              rmw_subscription_handle=2, topic_name='/topic', queue_depth=10),
        event('rclcpp_subscription_init', 0, subscription=3, subscription_handle=1),
        event('rclcpp_subscription_callback_added', 0, subscription=3, callback=500),
        event('rcl_service_init', 0, service_handle=4, node_handle=200,
              rmw_service_handle=5, service_name='/service'),
        event('rclcpp_service_callback_added', 0, service_handle=4, callback=600),
        *execution(1, 2), *execution(3, 4, callback=600)]
    repo, info = make_repository(events)
    data = rows(repo)
    assert [r['type'] for r in data] == ['subscription', 'service']
    assert data[0]['node_id'] != data[1]['node_id']
    assert not info['diagnostics'].get('incomplete_metadata')


def test_handle_reuse_does_not_change_earlier_ownership(make_repository):
    repo, _ = make_repository([node(), *timer(), *execution(1, 2),
        node(name='new_owner', t=3), *timer(t=3), *execution(4, 5)])
    data = rows(repo)
    assert data[0]['node_id'] != data[1]['node_id']
    assert repo.execution(data[0]['id'])['node_name'] == '/robot/alpha'
    assert repo.execution(data[1]['id'])['node_name'] == '/robot/new_owner'


def test_zero_duration_is_valid(make_repository):
    repo, _ = make_repository([node(), *timer(), *execution(1, 1)])
    assert rows(repo)[0]['duration_ns'] == 0


def test_scheduler_migration_and_preemption(make_repository):
    events = [node(), *timer()]
    events += [event('sched:sched_switch', 0, prev_tid=0, next_tid=101),
        event('callback_start', 10, callback=500),
        event('sched:sched_switch', 30, prev_tid=101, next_tid=22),
        event('sched:sched_switch', 50, prev_tid=0, next_tid=101, cpu_id=1),
        event('sched:sched_switch', 90, prev_tid=101, next_tid=22, cpu_id=1),
        event('callback_end', 100, callback=500, cpu_id=1)]
    for e in events:
        if e['_name'].startswith('ros2:callback_'):
            e['tid'] = 101
    repo, info = make_repository(events)
    assert info['capabilities']['cpu_execution']
    record = repo.execution(rows(repo)[0]['id'])
    assert record['duration_ns'] == '90'
    assert [(r['cpu'], int(r['start_ns']), int(r['end_ns'])) for r in record['cpu_intervals']] == [(0, 10, 30), (1, 50, 90)]
    cpu = repo.timeline(Filters(), 'cpu', 0, 100)
    assert len(cpu['records']) == 2


def test_no_scheduler_or_no_host_tid_never_guesses_cpu(repository, make_repository):
    assert not repository.info['capabilities']['cpu_execution']
    with pytest.raises(ValueError, match='sched_switch'):
        repository.timeline(Filters(), 'cpu', 0, 100)
    repo, info = make_repository([node(), *timer(),
        event('sched:sched_switch', 0, prev_tid=0, next_tid=11),
        *execution(1, 2), event('sched:sched_switch', 3, prev_tid=11, next_tid=0)])
    assert info['capabilities']['scheduler_events']
    assert not info['capabilities']['cpu_execution']
