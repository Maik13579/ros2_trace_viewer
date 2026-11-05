"""Recorded timer metadata and filtered, bounded timer callback statistics."""
import pytest
from fastapi.testclient import TestClient

from conftest import event, execution, node, timer
from test_communication import service
from test_publications import publisher, publish
from test_topics import subscription
from ros2_trace_viewer.server import create_app
from ros2_trace_viewer.trace.filters import Filters


@pytest.fixture
def timers_repository(make_repository):
    return make_repository([
        node(),node(200,'beta'),node(pid=2,name='other'),
        *timer(period=2_000_000,symbol='ZZZ::ZZZ()'),*timer(pid=2,period=0),
        # A known timer association without a recorded timer initialization.
        event('rclcpp_timer_callback_added',0,timer_handle=201,callback=600),
        event('rclcpp_timer_link_node',0,timer_handle=201,node_handle=200),
        event('rclcpp_callback_register',0,callback=600,symbol='AAA::AAA()'),
        *service(owner=200),*subscription(pid=1),publisher(),
        *execution(10,30),*execution(40,80),*publish(12),
        *execution(100,120,callback=600),*execution(15,25,pid=2),
        *execution(31,39,callback=800),*execution(2,8,callback=700),
        *execution(3,5,callback=999),event('rcl_init',150)])[0]


def test_timer_list_and_periods_are_process_scoped(timers_repository):
    repo=timers_repository
    data=repo.timers(Filters())
    assert data['total_groups']==3 and data['execution_count']==4
    assert data['total_callback_ns']=='90'
    assert all(row['type']=='timer' for row in data['rows'])
    periods={(row['pid'],row['node_name']):row['timer_period_ns'] for row in data['rows']}
    assert periods=={(1,'/robot/alpha'):'2000000',(1,'/robot/beta'):None,(2,'/robot/other'):'0'}
    first=repo.locate(Filters(types=('timer',)))
    assert first['timer_period_ns']=='2000000'
    assert repo.locate(Filters(types=('service',)))['timer_period_ns'] is None


def test_timer_filters_intersect_with_global_selection(timers_repository):
    repo=timers_repository
    assert repo.timers(Filters(types=('subscription',)))['rows']==[]
    assert repo.timers(Filters(none=True))['rows']==[]
    assert repo.timers(Filters(processes=(2,)))['execution_count']==1
    assert repo.timers(Filters(topics=('/output',)))['execution_count']==1
    assert repo.timers(Filters(services=('__none__',)))['execution_count']==4
    ranged=repo.timers(Filters(start=20,end=50))
    assert ranged['execution_count']==0 and ranged['crossing_count']==3


def test_timer_pages_sort_by_the_displayed_node_label(timers_repository):
    repo=timers_repository
    first=repo.timers(Filters(),sort='name',descending=False,limit=1)
    second=repo.timers(Filters(),sort='name',descending=False,offset=1,limit=1)
    assert first['rows'][0]['node_name']=='/robot/alpha'
    assert second['rows'][0]['node_name']=='/robot/beta'
    # Symbols remain available for details, but no longer determine node sorting.
    assert first['rows'][0]['name']=='ZZZ::ZZZ()'
    assert repo.boxplots(Filters(),sort='name',descending=False)['rows'][0]['node_name']=='/robot/alpha'


def test_period_is_precise_and_snapshotted_at_each_execution(make_repository):
    precise=9_007_199_254_741_111
    repo=make_repository([node(),*timer(period=precise),*execution(1,3),
        *timer(t=10,period=3_000_000),*execution(12,15)])[0]
    rows=repo.timers(Filters())['rows']
    assert {row['timer_period_ns'] for row in rows}=={str(precise),'3000000'}
    assert len({row['id'] for row in rows})==2


def test_timer_api_has_filtered_metrics_and_read_only_behavior(timers_repository):
    with TestClient(create_app(timers_repository.path)) as client:
        result=client.get('/api/timers?sort=name&descending=false&limit=1').json()
        assert result['rows'][0]['node_name']=='/robot/alpha'
        assert result['rows'][0]['timer_period_ns']=='2000000'
        assert result['total_groups']==3 and len(result['rows'])==1
        assert client.get('/api/timers?types=service').json()['rows']==[]
        assert client.get('/api/timers?none=1').json()['rows']==[]
        assert client.post('/api/timers').status_code==405
