from dataclasses import replace

import numpy as np
import pytest

from conftest import event, execution, node, timer
from ros2_trace_viewer.trace.filters import Filters, to_ns
from ros2_trace_viewer.trace.statistics import summarize


def stats(repository, filters=Filters(), level='callback', sort='total_ns', descending=True):
    return repository.statistics(filters, level, sort, descending, 0, 100)


def test_statistics_all_levels_and_percentage_denominator(repository):
    callbacks = stats(repository)
    assert callbacks['execution_count'] == 4
    assert callbacks['total_callback_ns'] == '100'
    assert callbacks['interval_ns'] == '100'
    assert [r['count'] for r in callbacks['rows']] == [2, 1, 1]
    assert [r['share_percent'] for r in callbacks['rows']] == [60, 30, 10]
    assert callbacks['rows'][0]['mean_ns'] == 30
    assert callbacks['rows'][0]['median_ns'] == 30
    assert callbacks['rows'][0]['stddev_ns'] == 10
    assert callbacks['rows'][0]['p95_ns'] == 39
    assert callbacks['rows'][0]['p99_ns'] == 39.8
    assert [r['total_ns'] for r in stats(repository, level='node')['rows']] == ['60', '30', '10']
    assert [r['share_percent'] for r in stats(repository, level='process')['rows']] == [90, 10]


def test_boundary_crossings_are_visible_but_not_aggregated(repository):
    active = Filters(start=20, end=50)
    result = stats(repository, active)
    assert result['execution_count'] == 1
    assert result['crossing_count'] == 3
    assert result['total_callback_ns'] == '30'
    assert result['rows'][0]['interval_percent'] == 100
    timeline = repository.timeline(active, 'node', 0, 100)
    assert len(timeline['records']) == 4
    assert all(int(r['duration_ns']) >= 0 for r in timeline['records'])


def test_overlapping_duration_ratio_can_exceed_one_hundred(make_repository):
    repo, _ = make_repository([node(), *timer(), *execution(0, 100), *execution(0, 100, tid=12)])
    result = stats(repo)
    assert result['rows'][0]['interval_percent'] == 200
    assert result['rows'][0]['share_percent'] == 100


@pytest.mark.parametrize('filters,count', [
    (Filters(processes=(1,)), 3), (Filters(exclude_processes=(1,)), 1),
    (Filters(threads=(12,)), 1), (Filters(types=('timer',)), 4),
    (Filters(types=('service',)), 0), (Filters(search='beta::work'), 1),
    (Filters(search='PROCESS_2'), 1), (Filters(search='definitely absent'), 0),
    (Filters(min_duration=20, max_duration=30), 2), (Filters(none=True), 0),
    (Filters(start=200, end=300), 0),
])
def test_shared_filter_semantics(repository, filters, count):
    assert stats(repository, filters)['execution_count'] == count
    assert repository.charts(filters)['count'] == count
    assert len(repository.timeline(filters, 'node', 0, 400)['records']) == count


def test_node_callback_hierarchical_exclusions(repository):
    first = stats(repository)['rows'][0]
    assert stats(repository, Filters(nodes=(first['node_id'],)))['execution_count'] == 2
    assert stats(repository, Filters(callbacks=(first['id'],)))['execution_count'] == 2
    assert stats(repository, Filters(exclude_callbacks=(first['id'],)))['execution_count'] == 2
    assert stats(repository, Filters(exclude_nodes=(first['node_id'],)))['execution_count'] == 2


def test_statistics_paging_and_quantile_sort(repository):
    result = repository.statistics(Filters(), 'callback', 'median_ns', False, 1, 1)
    assert result['total_groups'] == 3 and len(result['rows']) == 1
    assert result['rows'][0]['median_ns'] == 30
    result = stats(repository, sort='share_percent', descending=False)
    assert [row['share_percent'] for row in result['rows']] == [10, 30, 60]


def test_duration_quantiles_match_numpy():
    values = np.array([1, 1, 2, 3, 100], dtype=np.int64)
    result = summarize(values)
    assert result['median_ns'] == 2
    assert result['p95_ns'] == np.quantile(values, .95)
    assert result['stddev_ns'] == np.std(values)
    assert result['total_ns'] == '107'


def test_density_preserves_long_overlaps_in_all_bins(make_repository):
    events = [node(), *timer(), *execution(0, 100, tid=11)]
    for tid in range(12, 32):
        events += execution(10, 90, tid=tid)
    repo, _ = make_repository(events)
    result = repo.timeline(Filters(), 'node', 0, 100, budget=1, bins=16)
    assert result['mode'] == 'density'
    counts = {r['bin']: r['count'] for r in result['density']}
    assert counts[0] == 1 and counts[15] == 1
    assert counts[8] == 21
    assert len(result['density']) <= 16


def test_short_window_retrieves_long_callback_that_started_before_it(repository):
    result = repository.timeline(Filters(), 'node', 70, 71)
    assert len(result['records']) == 1
    assert result['records'][0]['start_ns'] == '40'
    assert result['records'][0]['end_ns'] == '80'


def test_navigation_uses_start_order_and_respects_filters(repository):
    current = repository.locate(Filters(processes=(1,)))
    next_record = repository.adjacent(current['id'], 'next', Filters())
    assert next_record['start_ns'] == '40'
    assert repository.adjacent(next_record['id'], 'previous', Filters())['id'] == current['id']
    assert repository.adjacent(current['id'], 'next', Filters(max_duration=20)) is None


@pytest.mark.parametrize('value', ['nan', 'inf', '-1', '9e99', 'invalid'])
def test_invalid_times(value):
    with pytest.raises(ValueError):
        to_ns(value)


def test_precise_time_filter_and_injection_is_literal(repository):
    assert to_ns('0.000000001') == 1
    assert to_ns('1234567.123456789') == 1234567123456789
    filters = Filters.parse({'search': "' OR 1=1 --", 'exclude_processes': '1,2'})
    assert stats(repository, filters)['execution_count'] == 0
    with pytest.raises(ValueError):
        Filters.parse({'start_time': '2', 'end_time': '1'})
    with pytest.raises(ValueError):
        Filters.parse({'cpus': 'oops'})


def test_metadata_tree_has_no_empty_placeholder_branches(repository, make_repository):
    assert repository.catalog('nodes')['total'] == 3
    assert all(item['name'] for item in repository.catalog('nodes')['items'])
    repo, _ = make_repository([node(), event('rclcpp_callback_register', 0,
        callback=500, symbol='unowned()'), *execution(1, 2)])
    unknown = repo.catalog('nodes')['items']
    assert len(unknown) == 1 and unknown[0]['id'] == 'unknown:1'
    assert repo.catalog('callbacks', node='unknown:1')['total'] == 1


def test_clear_then_select_multiple_branches(repository):
    callbacks=stats(repository)['rows']
    assert stats(repository,Filters(include_selection=True))['execution_count']==0
    active=Filters(include_selection=True,include_callbacks=(callbacks[0]['id'],))
    assert stats(repository,active)['execution_count']==2
    active=replace(active,include_processes=(2,))
    assert stats(repository,active)['execution_count']==3
    active=replace(active,exclude_callbacks=(callbacks[0]['id'],))
    assert stats(repository,active)['execution_count']==1
