"""Resource name search and namespace-independent standard service exclusion."""
import pytest
from fastapi.testclient import TestClient

from conftest import event, execution, node, timer
from test_communication import service
from test_publications import publisher, publish
from test_topics import subscription
from ros2_trace_viewer.server import create_app
from ros2_trace_viewer.trace.filters import Filters
from ros2_trace_viewer.trace.service_names import DEFAULT_SERVICE_SUFFIXES, is_default_service


@pytest.mark.parametrize('suffix', DEFAULT_SERVICE_SUFFIXES)
def test_default_service_suffixes_work_under_any_namespace(suffix):
    for prefix in ['', '/listener', '/robot/ns/reuse_timer']:
        assert is_default_service(prefix+suffix)
        assert not is_default_service(prefix+suffix+'_custom')
        assert not is_default_service(prefix+suffix+'/application')
    assert not is_default_service(None)


@pytest.fixture
def services_repository(make_repository):
    names=[prefix+suffix for prefix in ['', '/listener', '/robot/ns/node']
           for suffix in DEFAULT_SERVICE_SUFFIXES]
    names += ['/robot/set_parameters_extra','/get_parameters/application','/custom_service']
    events=[node(),*timer(),*execution(1,3)]
    for index,name in enumerate(names):
        callback, handle = 800+index,400+index
        events += service(callback,handle,name)+execution(10+index*5,13+index*5,callback=callback)
    events += service(900,600,'/unused/get_type_description')
    events += [event('rcl_client_init',0,client_handle=700,node_handle=100,
                     rmw_client_handle=701,service_name='/client/get_parameters')]
    return make_repository(events)[0]


def test_default_service_exclusion_covers_lists_and_every_aggregation(services_repository):
    repo=services_repository
    active=Filters.parse({'exclude_default_services':'1'})
    assert active.exclude_default_services
    assert repo.services(active)['total']==3
    assert {row['service'] for row in repo.services(active)['items']}=={
        '/robot/set_parameters_extra','/get_parameters/application','/custom_service'}
    assert len(repo.dimensions()['default_services'])==23
    assert all(not is_default_service(lane['id']) for lane in repo.timeline(active,'service',0,1000)['lanes'])
    assert len(repo.timeline(active,'node',0,1000)['records'])==4
    assert repo.statistics(active,'callback','total_ns',True,0,100)['execution_count']==4
    assert repo.boxplots(active)['execution_count']==4
    assert repo.charts(active)['count']==4
    # Explicit selection cannot reintroduce metadata-only or executed defaults.
    chosen=Filters(exclude_default_services=True,services=('/listener/set_parameters','/unused/get_type_description'))
    assert repo.services(chosen)['items']==[]
    assert repo.timeline(chosen,'service',0,1000)['lanes']==[]
    assert repo.services(Filters())['total']==26


def test_exclusion_and_service_search_api(services_repository):
    with TestClient(create_app(services_repository.path)) as client:
        data=client.get('/api/services?exclude_default_services=1').json()
        assert data['total']==3
        assert client.get('/api/statistics?exclude_default_services=1').json()['execution_count']==4
        assert client.get('/api/services?exclude_default_services=1&search=SeT_PaRaMeTeRs_ExTrA').json()['total']==1
        assert client.get('/api/services?exclude_default_services=1&service=/listener/get_parameters').json()['items']==[]
        assert client.get('/api/services?exclude_default_services=0').json()['total']==26


def test_topic_search_includes_publishing_callbacks_and_filters_their_publishes(make_repository):
    repo=make_repository([node(),*timer(),publisher(),node(pid=2,name='listener'),*subscription(),
        event('rcl_publisher_init',0,publisher_handle=910,node_handle=100,
              rmw_publisher_handle=911,topic_name='/rosout',queue_depth=10),
        *execution(1,20),*publish(2),event('rcl_publish',5,message=2000,publisher_handle=910),
        *execution(11,17,callback=700,pid=2,tid=22)])[0]
    active=Filters(search='OuTpUt')
    rows=repo.timeline(active,'node',0,20)['records']
    assert len(rows)==2
    publishing=next(row for row in rows if row['callback_type']=='timer')
    assert publishing['publish_count']==1
    assert [pub['topic'] for pub in publishing['publishes']]==['/output']
    assert repo.publications(publishing['id'],filters=active)['total']==1
    assert repo.statistics(active,'callback','total_ns',True,0,100)['execution_count']==2


@pytest.fixture
def mixed_repository(make_repository):
    """A service publishes only on topics the user has deselected."""
    return make_repository([
        node(name='talker'),node(200,'Server'),*timer(),
        *service(name='/add_two_ints',owner=200),
        *service(810,410,'/Server/get_parameters',owner=200),
        *service(820,420,'/declared_only',owner=200),
        publisher(topic='/chatter'),
        publisher(handle=910,rmw=911,owner=200,topic='/rosout'),
        publisher(handle=920,rmw=921,owner=200,topic='/parameter_events'),
        *execution(10,20),*publish(12),
        *execution(30,50,callback=800),
        *publish(35,handle=910,rmw=911,pointer=2000),
        *publish(40,handle=920,rmw=921,pointer=3000),
        *execution(60,70,callback=810),
        *publish(65,handle=920,rmw=921,pointer=4000),
        *publish(75,pointer=5000),event('rcl_init',100)])[0]


@pytest.mark.parametrize('topics', [('/chatter',), ('__none__',)])
def test_topic_selection_preserves_service_callbacks_and_declarations(mixed_repository,topics):
    repo=mixed_repository
    active=Filters(topics=topics,exclude_default_services=True)
    services=repo.services(active)
    assert {row['service'] for row in services['items']}=={'/add_two_ints','/declared_only'}
    recorded=next(row for row in services['items'] if row['service']=='/add_two_ints')
    assert recorded['execution_count']==1 and recorded['total_callback_ns']=='20'
    timeline=repo.timeline(active,'service',0,100)
    assert [r['callback_service'] for r in timeline['records']]==['/add_two_ints']
    assert timeline['records'][0]['publish_count']==0
    expected=2 if topics==('/chatter',) else 1
    assert len(repo.timeline(active,'node',0,100)['records'])==expected
    for level in ['process','node','callback']:
        assert repo.statistics(active,level,'total_ns',True,0,100)['execution_count']==expected
    assert repo.boxplots(active)['execution_count']==expected
    assert repo.charts(active)['count']==expected
    # Preserving the service callback must not bring its rejected topics back.
    topic_timeline=repo.timeline(active,'topic',0,100)
    assert all(p['topic']=='/chatter' for p in topic_timeline['publications'])
    assert all(r['lane']=='/chatter' for r in topic_timeline['records'])
    if topics==('/chatter',):
        assert len(topic_timeline['publications'])==2
    else:
        assert topic_timeline['lanes']==[]


def test_service_selection_preserves_topic_callbacks_and_unattributed_publishes(mixed_repository):
    repo=mixed_repository
    active=Filters(topics=('/chatter',),services=('__none__',),exclude_default_services=True)
    assert repo.services(active)['items']==[]
    assert repo.timeline(active,'service',0,100)['lanes']==[]
    records=repo.timeline(active,'node',0,100)['records']
    assert len(records)==1 and records[0]['callback_type']=='timer'
    assert repo.statistics(active,'callback','total_ns',True,0,100)['execution_count']==1
    assert repo.topics(active)['items'][0]['publish_count']==2
    assert len(repo.timeline(active,'topic',0,100)['publications'])==2


def test_scoped_resource_choices_still_obey_global_filters(mixed_repository):
    repo=mixed_repository
    active=Filters(topics=('/chatter',),services=('/add_two_ints',),exclude_default_services=True)
    assert repo.statistics(active,'callback','total_ns',True,0,100)['execution_count']==2
    restricted=Filters(topics=('/chatter',),exclude_default_services=True,min_duration=21)
    assert repo.statistics(restricted,'callback','total_ns',True,0,100)['execution_count']==0
    explicit=Filters(topics=('/chatter',),types=('timer',))
    assert repo.statistics(explicit,'callback','total_ns',True,0,100)['execution_count']==1
    empty=Filters(topics=('/chatter',),none=True)
    assert repo.services(empty)['items']==[]


def test_independent_topic_and_service_filters_through_api(mixed_repository):
    with TestClient(create_app(mixed_repository.path)) as client:
        params={'topics':'/chatter','exclude_default_services':'1'}
        service_rows=client.get('/api/services',params=params).json()['items']
        assert any(row['service']=='/add_two_ints' and row['execution_count']==1 for row in service_rows)
        timeline=client.get('/api/timeline',params={**params,'grouping':'service'}).json()
        assert timeline['records'][0]['callback_service']=='/add_two_ints'
        assert client.get('/api/statistics',params=params).json()['execution_count']==2
        assert client.get('/api/boxplots',params=params).json()['execution_count']==2
        other={**params,'services':'__none__'}
        assert client.get('/api/services',params=other).json()['items']==[]
        assert client.get('/api/topics',params=other).json()['items'][0]['publish_count']==2


@pytest.fixture
def mixed_repository(make_repository):
    """A service publishes only on topics the user has deselected."""
    return make_repository([
        node(name='talker'),node(200,'Server'),*timer(),
        *service(name='/add_two_ints',owner=200),
        *service(810,410,'/Server/get_parameters',owner=200),
        *service(820,420,'/declared_only',owner=200),
        publisher(topic='/chatter'),
        publisher(handle=910,rmw=911,owner=200,topic='/rosout'),
        publisher(handle=920,rmw=921,owner=200,topic='/parameter_events'),
        *execution(10,20),*publish(12),
        *execution(30,50,callback=800),
        *publish(35,handle=910,rmw=911,pointer=2000),
        *publish(40,handle=920,rmw=921,pointer=3000),
        *execution(60,70,callback=810),
        *publish(65,handle=920,rmw=921,pointer=4000),
        *publish(75,pointer=5000),event('rcl_init',100)])[0]


@pytest.mark.parametrize('topics', [('/chatter',), ('__none__',)])
def test_topic_selection_preserves_service_callbacks_and_declarations(mixed_repository,topics):
    repo=mixed_repository
    active=Filters(topics=topics,exclude_default_services=True)
    services=repo.services(active)
    assert {row['service'] for row in services['items']}=={'/add_two_ints','/declared_only'}
    recorded=next(row for row in services['items'] if row['service']=='/add_two_ints')
    assert recorded['execution_count']==1 and recorded['total_callback_ns']=='20'
    timeline=repo.timeline(active,'service',0,100)
    assert [r['callback_service'] for r in timeline['records']]==['/add_two_ints']
    assert timeline['records'][0]['publish_count']==0
    expected=2 if topics==('/chatter',) else 1
    assert len(repo.timeline(active,'node',0,100)['records'])==expected
    for level in ['process','node','callback']:
        assert repo.statistics(active,level,'total_ns',True,0,100)['execution_count']==expected
    assert repo.boxplots(active)['execution_count']==expected
    assert repo.charts(active)['count']==expected
    # Preserving the service callback must not bring its rejected topics back.
    topic_timeline=repo.timeline(active,'topic',0,100)
    assert all(p['topic']=='/chatter' for p in topic_timeline['publications'])
    assert all(r['lane']=='/chatter' for r in topic_timeline['records'])
    if topics==('/chatter',):
        assert len(topic_timeline['publications'])==2
    else:
        assert topic_timeline['lanes']==[]


def test_service_selection_preserves_topic_callbacks_and_unattributed_publishes(mixed_repository):
    repo=mixed_repository
    active=Filters(topics=('/chatter',),services=('__none__',),exclude_default_services=True)
    assert repo.services(active)['items']==[]
    assert repo.timeline(active,'service',0,100)['lanes']==[]
    records=repo.timeline(active,'node',0,100)['records']
    assert len(records)==1 and records[0]['callback_type']=='timer'
    assert repo.statistics(active,'callback','total_ns',True,0,100)['execution_count']==1
    assert repo.topics(active)['items'][0]['publish_count']==2
    assert len(repo.timeline(active,'topic',0,100)['publications'])==2


def test_scoped_resource_choices_still_obey_global_filters(mixed_repository):
    repo=mixed_repository
    active=Filters(topics=('/chatter',),services=('/add_two_ints',),exclude_default_services=True)
    assert repo.statistics(active,'callback','total_ns',True,0,100)['execution_count']==2
    restricted=Filters(topics=('/chatter',),exclude_default_services=True,min_duration=21)
    assert repo.statistics(restricted,'callback','total_ns',True,0,100)['execution_count']==0
    explicit=Filters(topics=('/chatter',),types=('timer',))
    assert repo.statistics(explicit,'callback','total_ns',True,0,100)['execution_count']==1
    empty=Filters(topics=('/chatter',),none=True)
    assert repo.services(empty)['items']==[]


def test_independent_topic_and_service_filters_through_api(mixed_repository):
    with TestClient(create_app(mixed_repository.path)) as client:
        params={'topics':'/chatter','exclude_default_services':'1'}
        service_rows=client.get('/api/services',params=params).json()['items']
        assert any(row['service']=='/add_two_ints' and row['execution_count']==1 for row in service_rows)
        timeline=client.get('/api/timeline',params={**params,'grouping':'service'}).json()
        assert timeline['records'][0]['callback_service']=='/add_two_ints'
        assert client.get('/api/statistics',params=params).json()['execution_count']==2
        assert client.get('/api/boxplots',params=params).json()['execution_count']==2
        other={**params,'services':'__none__'}
        assert client.get('/api/services',params=other).json()['items']==[]
        assert client.get('/api/topics',params=other).json()['items'][0]['publish_count']==2
