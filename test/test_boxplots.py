"""Exact callback distributions and bounded, filtered box-plot responses."""
import numpy as np
import pytest

from conftest import event, execution, node, timer
from ros2_trace_viewer.trace.filters import Filters
from ros2_trace_viewer.trace.statistics import summarize


def test_tukey_quartiles_whiskers_and_outlier():
    result = summarize(np.array([1, 2, 3, 4, 5, 6, 7, 8, 9, 100]), include_boxplot=True)
    assert result['q1_ns'] == 3.25
    assert result['median_ns'] == 5.5
    assert result['q3_ns'] == 7.75
    assert result['lower_whisker_ns'] == '1'
    assert result['upper_whisker_ns'] == '9'
    assert result['outlier_count'] == 1
    assert result['outliers_ns'] == ['100']
    assert not result['outliers_sampled']


@pytest.mark.parametrize('values', [[0], [5], [5, 5, 5], [1, 2]])
def test_single_constant_zero_and_two_sample_callbacks(values):
    result = summarize(np.array(values, dtype=np.int64), include_boxplot=True)
    assert result['q1_ns'] == np.quantile(values, .25)
    assert result['median_ns'] == np.quantile(values, .5)
    assert result['q3_ns'] == np.quantile(values, .75)
    assert int(result['lower_whisker_ns']) == min(values)
    assert int(result['upper_whisker_ns']) == max(values)
    assert result['outlier_count'] == 0
    assert result['outliers_ns'] == []


def test_lower_and_upper_outliers_use_observed_whiskers():
    values = np.array([0, *range(90, 101), 1000], dtype=np.int64)
    result = summarize(values, include_boxplot=True)
    assert result['lower_whisker_ns'] == '90'
    assert result['upper_whisker_ns'] == '100'
    assert result['outliers_ns'] == ['0', '1000']
    assert result['outlier_count'] == 2


def test_outlier_sampling_is_bounded_and_count_is_exact():
    values = np.concatenate([np.zeros(1000, dtype=np.int64), np.arange(1000, 1300)])
    result = summarize(values, include_boxplot=True)
    assert result['outlier_count'] == 300
    assert len(result['outliers_ns']) == 64
    assert result['outliers_sampled']
    assert result['outliers_ns'][0] == '1000'
    assert result['outliers_ns'][-1] == '1299'
    assert set(map(int, result['outliers_ns'])) <= set(values)
    assert result == summarize(values, include_boxplot=True)


def test_callback_paging_retains_common_axis_and_process_identity(repository):
    first = repository.boxplots(Filters(), sort='total_ns', offset=0, limit=1)
    second = repository.boxplots(Filters(), sort='total_ns', offset=1, limit=1)
    assert first['total_groups'] == second['total_groups'] == 3
    assert first['axis_max_ns'] == second['axis_max_ns'] == '40'
    assert first['execution_count'] == 4
    record = first['rows'][0]
    assert record['q1_ns'] == 25 and record['q3_ns'] == 35
    assert record['median_ns'] == 30 and record['count'] == 2
    assert record['lower_whisker_ns'] == '20' and record['upper_whisker_ns'] == '40'
    assert sum(row['count'] for row in repository.boxplots(Filters())['rows']) == 4


def test_filters_and_boundary_policy_match_statistics(repository):
    active = Filters(start=20, end=50)
    result = repository.boxplots(active)
    assert result['execution_count'] == 1 and result['crossing_count'] == 3
    assert result['axis_max_ns'] == '30'
    assert result['rows'][0]['count'] == 1
    assert result['rows'][0]['q1_ns'] == result['rows'][0]['q3_ns'] == 30
    filtered = repository.boxplots(Filters(search='alpha', processes=(2,)))
    assert len(filtered['rows']) == 1 and filtered['rows'][0]['pid'] == 2
    assert filtered['rows'][0]['median_ns'] == 10
    empty = repository.boxplots(Filters(none=True))
    assert empty['rows'] == [] and empty['axis_max_ns'] == '0'


def test_uninitialized_callbacks_do_not_get_boxplots(make_repository):
    repository, _ = make_repository([node(), *timer(), *execution(1, 2),
        *execution(3, 4, callback=999), event('rcl_init', 10)])
    result = repository.boxplots(Filters())
    assert result['execution_count'] == 1 and result['total_groups'] == 1


def test_boxplot_details_are_opt_in_for_statistics(repository):
    result = repository.statistics(Filters(), 'callback', 'total_ns', True, 0, 100)
    assert all('outliers_ns' not in row for row in result['rows'])
