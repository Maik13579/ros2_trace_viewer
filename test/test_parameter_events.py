"""Parameter event hiding applies to metadata, markers and callback statistics."""
from fastapi.testclient import TestClient

from conftest import node, timer, execution, event
from test_publications import publisher, publish
from test_topics import subscription
from test_communication import service
from ros2_trace_viewer.server import create_app
from ros2_trace_viewer.trace.filters import Filters
from ros2_trace_viewer.trace.topic_names import is_parameter_events


def parameter_repository(make_repository):
    return make_repository([node(),node(pid=2),*timer(),
        publisher(topic='/parameter_events'),
        publisher(handle=910,rmw=911,topic='/ordinary'),
        publisher(handle=920,rmw=921,topic='/ns/parameter_events'),
        publisher(handle=930,rmw=931,topic='/ns/parameter_events_extra'),
        *subscription(topic='/parameter_events'),
        *service(name='/ns/node/get_parameters'),
        *service(810,410,'/add_two_ints'),
        *execution(1,20),*publish(2),
        event('rcl_publish',9,message=2000,publisher_handle=910),
        event('rcl_publish',10,message=3000,publisher_handle=920),
        event('rcl_publish',11,message=4000,publisher_handle=930),
        *execution(5,8,callback=700,pid=2),
        *execution(22,25,callback=800),*execution(26,29,callback=810)])[0]


def test_parameter_events_are_exact_namespaced_suffixes():
    assert is_parameter_events('/parameter_events')
    assert is_parameter_events('/robot/node/parameter_events')
    assert not is_parameter_events('/robot/parameter_events_extra')
    assert not is_parameter_events('/parameter_events/data')


def test_hide_parameter_events_keeps_application_data_and_services(make_repository):
    repo = parameter_repository(make_repository)
    active = Filters.parse({'exclude_parameter_events':'1','exclude_default_services':'1'})
    assert active.exclude_parameter_events
    assert {row['topic'] for row in repo.topics(active)['items']} == {'/ordinary','/ns/parameter_events_extra'}
    assert repo.statistics(active,'callback','total_ns',True,0,100)['execution_count'] == 2
    assert repo.boxplots(active)['execution_count'] == 2
    assert repo.services(active)['items'][0]['service'] == '/add_two_ints'
    data = repo.timeline(active,'node',0,30)
    timer_row = next(row for row in data['records'] if row['callback_type']=='timer')
    assert timer_row['publish_count'] == 2
    assert all(not is_parameter_events(point['topic']) for point in timer_row['publishes'])
    assert repo.publications(timer_row['id'],filters=active)['total'] == 2
    assert all(not is_parameter_events(lane['id']) for lane in repo.timeline(active,'topic',0,30)['lanes'])
    # Catalog metadata retains the choices so the user can show them again.
    assert '/parameter_events' in repo.dimensions()['topics']


def test_parameter_visibility_can_be_restored_explicitly(make_repository):
    repo = parameter_repository(make_repository)
    with TestClient(create_app(repo.path)) as client:
        hidden = client.get('/api/topics?exclude_parameter_events=1').json()
        shown = client.get('/api/topics?exclude_parameter_events=0').json()
        assert hidden['total']==2 and shown['total']==4
        assert client.get('/api/topics?exclude_parameter_events=1&topic=/parameter_events').json()['items']==[]
        assert client.get('/api/topics?exclude_parameter_events=0&topic=/parameter_events').json()['total']==1
        hidden_stats = client.get('/api/statistics?exclude_parameter_events=1&exclude_default_services=1').json()
        assert hidden_stats['execution_count']==2
