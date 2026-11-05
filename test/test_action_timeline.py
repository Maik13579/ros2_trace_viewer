"""Actions have separate filters, real intervals and bounded transport markers."""
from dataclasses import replace
import pytest

from fastapi.testclient import TestClient

from conftest import event, execution, timer
from test_actions import ACTION, action_events
from test_communication import service
from test_publications import publisher
from test_topics import subscription
from ros2_trace_viewer.server import create_app
from ros2_trace_viewer.trace.filters import Filters


def timeline(repo, filters=Filters(), **kwargs):
    return repo.timeline(filters,'action',0,int(repo.info['duration_ns']),**kwargs)


def test_actions_are_separate_from_topic_and_service_catalogs(make_repository):
    repo = make_repository([*action_events(),publisher(handle=999,rmw=998,topic='/ordinary'),
                            *service(name='/ordinary_service')])[0]
    dimensions = repo.dimensions()
    assert dimensions['actions'] == [ACTION]
    assert dimensions['topics'] == ['/ordinary']
    assert dimensions['services'] == ['/ordinary_service']
    assert {row['topic'] for row in repo.topics(Filters())['items']} == {'/ordinary'}
    assert {row['service'] for row in repo.services(Filters())['items']} == {'/ordinary_service'}
    for role in ('send_goal','get_result','cancel_goal'):
        assert repo.services(Filters(),service=ACTION+'/_action/'+role)['total'] == 1
    assert repo.topics(Filters(),topic=ACTION+'/_action/feedback')['total'] == 1
    assert not any('/_action/' in lane['id'] for lane in repo.timeline(Filters(),'topic',0,30)['lanes'])
    assert not any('/_action/' in lane['id'] for lane in repo.timeline(Filters(),'service',0,30)['lanes'])


def test_transport_markers_do_not_create_fake_callback_or_goal_spans(make_repository):
    repo = make_repository(action_events())[0]
    data = timeline(repo)
    assert data['lanes'] == [{'id':ACTION,'label':ACTION}]
    assert data['records'] == [] and data['goal_timing_available'] is False
    assert len(data['publications']) == len(data['receives']) == 3
    assert {point['action_role'] for point in data['publications']} == {'feedback','status'}
    for key, kind in [('publications','publish'),('receives','receive')]:
        assert all(point['event_kind']==kind and point['action']==point['lane']==ACTION for point in data[key])
        assert all('duration_ns' not in point for point in data[key])
    assert repo.receive(data['receives'][0]['id'],Filters())['node_name'] == '/robot/client'


def test_action_filters_are_namespaced_and_independent(make_repository):
    other = '/robot/two/fibonacci'
    repo = make_repository([*action_events(),*action_events(other,server=3,client=4)])[0]
    active = Filters.parse({'actions':ACTION,'topics':'__none__','services':'__none__'})
    assert active.actions == (ACTION,)
    data = timeline(repo,active)
    assert [lane['id'] for lane in data['lanes']] == [ACTION]
    assert len(data['publications']) == len(data['receives']) == 3
    assert timeline(repo,replace(active,actions=('__none__',)))['lanes'] == []
    data = timeline(repo,replace(active,actions=(other,)))
    assert all(point['pid']==3 for point in data['publications'])
    assert all(point['pid']==4 for point in data['receives'])
    assert timeline(repo,lane_offset=1,lane_limit=1)['lanes'][0]['id'] == other


def test_action_marker_time_owner_search_and_strict_filters(make_repository):
    repo = make_repository(action_events())[0]
    data = timeline(repo,Filters(start=6,end=18))
    assert [point['timestamp_ns'] for point in data['publications']] == ['12']
    assert [point['timestamp_ns'] for point in data['receives']] == ['14']
    assert timeline(repo,Filters(processes=(1,)))['receives'] == []
    assert timeline(repo,Filters(processes=(2,)))['publications'] == []
    assert timeline(repo,Filters(search='no-such-action'))['lanes'] == []
    assert timeline(repo,Filters(none=True))['lanes'] == []
    assert timeline(repo,Filters(types=('service',)))['receives'] == []
    result = repo.timeline(Filters(),'action',20,30)
    assert [point['timestamp_ns'] for point in result['publications']] == ['20']
    assert [point['timestamp_ns'] for point in result['receives']] == ['21']


def test_real_callback_spans_are_retained_once_per_action(make_repository):
    repo = make_repository([*action_events(),*timer(),*execution(1,25),
        *subscription(topic=ACTION+'/_action/feedback'),*execution(6,9,callback=700,pid=2),
        *service(name=ACTION+'/_action/send_goal'),*execution(26,29,callback=800)])[0]
    data = timeline(repo)
    assert len(data['records']) == 3
    assert {row['callback_type'] for row in data['records']} == {'timer','subscription','service'}
    timer_row = next(row for row in data['records'] if row['callback_type']=='timer')
    assert timer_row['start_ns']=='1' and timer_row['end_ns']=='25'
    assert timer_row['duration_ns']=='24' and timer_row['publish_count']==3
    assert len({row['id'] for row in data['records']}) == 3
    narrow = repo.timeline(Filters(),'action',8,9)
    assert len(narrow['records']) == 2 and narrow['publications'] == []
    assert next(row for row in narrow['records'] if row['callback_type']=='timer')['start_ns']=='1'
    data = timeline(repo,Filters(topics=('__none__',),services=('__none__',)))
    assert len(data['records']) == 3
    strict = timeline(repo,Filters(types=('timer',)))
    assert strict['receives'] == [] and strict['receives_attributable'] is False
    assert len(strict['publications']) == 3
    hidden = Filters(actions=('__none__',))
    assert repo.statistics(hidden,'callback','total_ns',True,0,100)['execution_count'] == 0
    assert repo.boxplots(hidden)['rows'] == []


def test_action_selection_keeps_unrelated_callbacks_and_topics(make_repository):
    repo = make_repository([*action_events(),*timer(),*execution(6,9),
        publisher(handle=999,rmw=998,topic='/ordinary'),
        event('rcl_publish',7,message=2000,publisher_handle=999)])[0]
    hidden = Filters(actions=('__none__',))
    assert repo.statistics(hidden,'callback','total_ns',True,0,100)['execution_count'] == 1
    assert repo.topics(hidden)['items'][0]['topic'] == '/ordinary'
    assert repo.timeline(hidden,'node',0,30)['records'][0]['publish_count'] == 1


def test_dense_transport_is_bounded_and_counts_every_observation(make_repository):
    events = action_events() + [event('rcl_publish',i+31,message=3000+i,publisher_handle=900)
        for i in range(1000)]
    events += [event('rmw_take',i+31,pid=2,rmw_subscription_handle=350,message=5000+i,taken=1)
        for i in range(1000)]
    repo = make_repository(events)[0]
    data = timeline(repo,budget=10,bins=32)
    assert data['publications_mode'] == data['receives_mode'] == 'density'
    assert len(data['publications'])+len(data['receives']) <= 10
    assert sum(point['count'] for point in data['publications']) == 1003
    assert sum(point['count'] for point in data['receives']) == 1003
    assert all('id' not in point for point in data['receives'])


def test_callback_density_preserves_overlapping_action_intervals(make_repository):
    events = [*action_events(),*subscription(topic=ACTION+'/_action/feedback')]
    for index in range(100): events += execution(index+1,index+101,callback=700,pid=2,tid=index+100)
    repo = make_repository(events)[0]
    data = timeline(repo,budget=2,bins=32)
    assert data['mode']=='density' and max(cell['count'] for cell in data['density'])==100
    assert {cell['lane'] for cell in data['density']} == {ACTION}


def test_mixed_publishing_callback_keeps_only_the_selected_action_markers(make_repository):
    other = '/robot/two/fibonacci'
    repo = make_repository([*action_events(),*timer(),*execution(1,25),
        publisher(handle=910,rmw=960,topic=other+'/_action/feedback'),
        event('rcl_publish',10,message=5000,publisher_handle=910)])[0]
    data = timeline(repo)
    assert len(data['records']) == 2
    assert len({row['id'] for row in data['records']}) == 1
    assert {row['lane']:row['publish_count'] for row in data['records']} == {ACTION:3,other:1}
    active = Filters(actions=(ACTION,))
    node_data = repo.timeline(active,'node',0,30)
    row = node_data['records'][0]
    assert row['publish_count'] == 3
    assert all(point['topic'].startswith(ACTION+'/_action/') for point in row['publishes'])
    assert repo.publications(row['id'],filters=active)['total'] == 3
    assert timeline(repo,active)['lanes'] == [{'id':ACTION,'label':ACTION}]


def test_receive_api_respects_active_filters(make_repository):
    repo = make_repository(action_events())[0]
    receive = timeline(repo)['receives'][0]
    with TestClient(create_app(repo.path)) as client:
        assert client.get('/api/timeline?grouping=action').json()['receives']
        path = f"/api/receives/{receive['id']}"
        assert client.get(path).json()['topic'] == ACTION+'/_action/feedback'
        assert client.get(path,params={'actions':'__none__'}).status_code == 404
        assert client.get(path,params={'processes':'1'}).status_code == 404
        assert client.get(path,params={'start_time':'0.000000006'}).status_code == 404
        assert client.post(path).status_code == 405


@pytest.mark.parametrize('grouping',['node','process','thread'])
def test_action_activity_is_visible_without_action_columns(make_repository,grouping):
    repo = make_repository(action_events())[0]
    data = repo.timeline(Filters(),grouping,0,30)
    assert data['records'] == []
    assert len(data['lanes']) == 2
    assert len(data['publications']) == len(data['receives']) == 3
    lanes = {lane['id'] for lane in data['lanes']}
    assert {point['lane'] for point in data['publications']+data['receives']} <= lanes
    assert all(point['pid']==1 for point in data['publications'])
    assert all(point['pid']==2 for point in data['receives'])
    hidden = repo.timeline(Filters(actions=('__none__',)),grouping,0,30)
    assert hidden['lanes'] == []
    narrow = repo.timeline(Filters(),grouping,10,18)
    assert [point['timestamp_ns'] for point in narrow['publications']] == ['12']
    page = repo.timeline(Filters(),grouping,0,30,lane_offset=1,lane_limit=1)
    assert len(page['lanes']) == 1
    assert all(point['lane']==page['lanes'][0]['id'] for point in page['publications']+page['receives'])


@pytest.mark.parametrize('grouping',['node','process','thread'])
def test_owner_action_density_is_bounded_and_keeps_counts(make_repository,grouping):
    events = action_events()+[event('rcl_publish',31+i,message=5000+i,publisher_handle=900)
        for i in range(1000)]
    repo = make_repository(events)[0]
    data = repo.timeline(Filters(),grouping,0,1030,budget=10,bins=32)
    assert data['publications_mode']=='density'
    assert len(data['publications']) <= 5
    assert sum(point['count'] for point in data['publications']) == 1003
