"""Topics expose declarations/activity, not per-message delivery claims."""
from conftest import EPOCH, event, execution, node, timer
from test_publications import publisher, publish
from ros2_trace_viewer.trace.filters import Filters


def subscription(pid=2, topic='/output', callback=700):
    return [event('rcl_subscription_init', 0, pid=pid, subscription_handle=300, node_handle=100,
                  rmw_subscription_handle=301, topic_name=topic, queue_depth=10),
            event('rclcpp_subscription_init', 0, pid=pid, subscription=302, subscription_handle=300),
            event('rclcpp_subscription_callback_added', 0, pid=pid, subscription=302, callback=callback),
            event('rclcpp_callback_register', 0, pid=pid, callback=callback, symbol='Listener::Listener()')]


def make_topics(make_repository):
    return make_repository([node(), *timer(), publisher(), node(pid=2, name='beta'), *subscription(),
        *execution(1, 20), *publish(2), *publish(21),
        event('rmw_take', 10, pid=2, tid=22, taken=1, rmw_subscription_handle=301,
              message=5000, source_timestamp=EPOCH+2), *execution(11, 17, callback=700, pid=2, tid=22),
        event('rcl_init', 30)])[0]


def test_subscription_topic_is_available_everywhere(make_repository):
    repo = make_topics(make_repository)
    record = repo.locate(Filters(processes=(2,)))
    assert record['callback_topic'] == '/output'
    assert record['node_name'] == '/robot/beta'
    assert repo.catalog('callbacks', search='/output')['total'] == 1
    assert repo.statistics(Filters(search='/output'), 'callback', 'name', False, 0, 100)['execution_count'] == 2
    assert repo.boxplots(Filters(processes=(2,)))['rows'][0]['callback_topic'] == '/output'


def test_topic_activity_and_static_endpoint_association(make_repository):
    repo = make_topics(make_repository)
    result = repo.topics(Filters(), topic='/output')
    assert result['total'] == 1
    row = result['items'][0]
    assert row['publish_count'] == 2 and row['receive_count'] == 1
    assert row['publisher_nodes'] == ['/robot/alpha']
    assert row['subscriber_nodes'] == ['/robot/beta']
    assert row['publishers'][0]['queue_depth'] == row['subscriptions'][0]['queue_depth'] == 10
    assert row['first_ns'] == '2' and row['last_ns'] == '21'
    assert row['execution_count'] == 1 and row['mean_duration_ns'] == 6
    assert 'not verified' in result['association']


def test_topic_interval_search_and_empty_selection(make_repository):
    repo = make_topics(make_repository)
    row = repo.topics(Filters(start=8, end=18))['items'][0]
    assert row['publish_count'] == 0 and row['receive_count'] == 1
    assert row['first_ns'] == row['last_ns'] == '10'
    assert repo.topics(Filters(none=True))['items'] == []
    assert repo.topics(Filters(), search='not-present')['items'] == []
    assert repo.topics(Filters(search='/output'))['total'] == 1


def test_receive_counts_are_unknown_under_callback_filters(make_repository):
    repo = make_topics(make_repository)
    callback = repo.locate(Filters(processes=(1,)))['callback_id']
    result = repo.topics(Filters(callbacks=(callback,)))
    assert result['receives_attributable'] is False
    assert result['items'][0]['receive_count'] is None
    assert result['items'][0]['publish_count'] == 1
