"""Publication scopes, buffer reuse, tracepoint layers, and bounded markers."""
from conftest import EPOCH, event, execution, node, timer
from ros2_trace_viewer.trace.filters import Filters


def publisher(pid=1, handle=900, rmw=901, owner=100, topic='/output'):
    return event('rcl_publisher_init', 0, pid=pid, publisher_handle=handle,
                 node_handle=owner, rmw_publisher_handle=rmw, topic_name=topic, queue_depth=10)


def publish(t, pid=1, tid=11, pointer=1000, handle=900, rmw=901):
    return [event('rclcpp_publish', t, pid=pid, tid=tid, message=pointer),
            event('rcl_publish', t+1, pid=pid, tid=tid, message=pointer, publisher_handle=handle),
            event('rmw_publish', t+2, pid=pid, tid=tid, message=pointer,
                  rmw_publisher_handle=rmw, timestamp=EPOCH+t)]


def test_layers_are_combined_and_topics_are_resolved(make_repository):
    repo, info = make_repository([node(), *timer(), publisher(), *execution(1, 10), *publish(3)])
    execution_record = repo.locate(Filters())
    assert execution_record['publish_count'] == 1
    result = repo.publications(execution_record['id'])
    assert result['total'] == 1
    record = result['items'][0]
    assert record['topic'] == '/output' and record['node_name'] == '/robot/alpha'
    assert record['timestamp_ns'] == '3' and record['layers'] == 7
    assert record['source_timestamp'] == str(EPOCH+3)
    assert record['message_pointer'] == '0x3e8'
    assert len(record['tracepoints']) == 3
    assert repo.publication(record['id'])['execution']['id'] == execution_record['id']
    assert info['diagnostics']['publish_tracepoints'] == 3
    assert info['diagnostics']['publishes'] == 1
    assert repo.publication(99999) is None


def test_reused_pointer_and_different_processes_threads(make_repository):
    repo, _ = make_repository([node(), *timer(), publisher(), node(pid=2), *timer(pid=2), publisher(pid=2),
        *execution(1, 30), *execution(2, 29, tid=12), *execution(1, 30, pid=2),
        *publish(3), *publish(8), *publish(3, tid=12), *publish(3, pid=2)])
    with repo.connect() as db:
        rows = [tuple(r) for r in db.execute('SELECT pid,tid,count(*) FROM publications GROUP BY pid,tid ORDER BY pid,tid')]
    assert rows == [(1, 11, 2), (1, 12, 1), (2, 11, 1)]
    assert all(repo.publication(i)['execution_id'] is not None for i in range(1, 5))


def test_nested_callback_owns_its_publishes(make_repository):
    repo, _ = make_repository([node(), *timer(), *timer(600, handle=201), publisher(),
        *execution(1, 20), *execution(5, 10, callback=600), *publish(6), *publish(13)])
    first, second = repo.publication(1), repo.publication(2)
    assert first['execution']['callback_identity'] == hex(600)
    assert second['execution']['callback_identity'] == hex(500)
    assert first['execution']['publish_count'] == second['execution']['publish_count'] == 1


def test_outside_and_incomplete_scopes_are_not_reattributed(make_repository):
    repo, info = make_repository([node(), *timer(), publisher(), *publish(1),
        event('callback_start', 5, callback=500), *publish(6)])
    assert repo.publication(1)['callback_status'] == 'outside'
    assert repo.publication(2)['callback_status'] == 'incomplete'
    assert repo.publication(2)['execution'] is None
    assert info['diagnostics']['missing_end'] == 1


def test_loss_prevents_cross_gap_layer_matching(make_repository):
    repo, _ = make_repository([node(), *timer(), publisher(), event('callback_start', 1, callback=500),
        event('rclcpp_publish', 2, message=1000), event('rcl_init', 3, _discarded=1),
        event('rcl_publish', 4, message=1000, publisher_handle=900),
        event('callback_end', 5, callback=500)])
    assert repo.publication(1)['callback_status'] == 'incomplete'
    assert repo.publication(2)['callback_status'] == 'outside'


def test_different_publisher_handles_do_not_merge(make_repository):
    repo, _ = make_repository([node(), *timer(), publisher(), publisher(handle=910, rmw=911, topic='/other'),
        *execution(1, 10), event('rcl_publish', 3, message=1000, publisher_handle=900),
        event('rmw_publish', 4, message=1000, rmw_publisher_handle=911, timestamp=EPOCH+4)])
    assert repo.publication(1)['topic'] == '/output'
    assert repo.publication(2)['topic'] == '/other'


def test_intra_process_path_is_separate(make_repository):
    repo, _ = make_repository([node(), *timer(), publisher(), *execution(1, 20),
        event('rclcpp_intra_publish', 3, message=1000, publisher_handle=900), *publish(4)])
    records = repo.publications(repo.locate(Filters())['id'])['items']
    assert [record['layers'] for record in records] == [8, 7]


def test_visible_window_and_density_budget_preserve_counts(make_repository):
    events = [node(), *timer(), publisher(), *execution(1, 30000)]
    events += [event('rcl_publish', i+2, message=1000, publisher_handle=900) for i in range(20500)]
    repo, _ = make_repository(events)
    data = repo.timeline(Filters(), 'node', 0, 30001)
    assert data['publish_markers_mode'] == 'density'
    record = data['records'][0]
    assert record['publish_count'] == 20500
    assert len(record['publishes']) <= 256
    assert sum(marker['count'] for marker in record['publishes']) == 20500
    # Rows flushed before callback_end must still acquire the completed execution.
    assert repo.publication(1)['execution_id'] == record['id']
    narrow = repo.timeline(Filters(), 'node', 100, 110)
    assert len(narrow['records'][0]['publishes']) == 11
    assert all(100 <= int(p['timestamp_ns']) <= 110 for p in narrow['records'][0]['publishes'])
