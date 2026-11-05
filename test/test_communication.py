"""Resource filters and bounded topic/service timeline observations."""
from dataclasses import replace

from conftest import event, execution, node, timer
from test_publications import publisher, publish
from test_topics import make_topics, subscription
from ros2_trace_viewer.trace.filters import Filters


def service(callback=800, handle=400, name='/add', pid=1, owner=100):
    return [event('rcl_service_init', 0, pid=pid, service_handle=handle,
                  node_handle=owner, rmw_service_handle=handle+1, service_name=name),
            event('rclcpp_service_callback_added', 0, pid=pid, service_handle=handle, callback=callback),
            event('rclcpp_callback_register', 0, pid=pid, callback=callback, symbol='Server::handle()')]


def service_repository(make_repository):
    return make_repository([node(), node(200, 'server_b'), node(pid=2, name='client'),
        *service(), *service(800,400,'/other',2), *service(900,500,'/second',1,200),
        event('rcl_client_init',0,pid=2,client_handle=600,node_handle=100,
              rmw_client_handle=601,service_name='/add'),
        *execution(1,6,callback=800), *execution(10,20,callback=800),
        *execution(2,8,callback=900,tid=12), *execution(2,5,callback=800,pid=2),
        event('rcl_init',30)])[0]


def test_topic_filter_includes_publishers_and_subscription_callbacks(make_repository):
    repo=make_topics(make_repository)
    filters=Filters.parse({'topics':'/output'})
    assert filters.topics==('/output',)
    result=repo.statistics(filters,'process','total_ns',True,0,100)
    assert result['execution_count']==2 and result['total_callback_ns']=='25'
    assert repo.boxplots(filters)['execution_count']==2
    assert repo.topics(filters)['total']==1
    assert repo.topics(Filters(topics=('/missing',)))['items']==[]
    assert repo.statistics(Filters(topics=('/missing',)),'process','total_ns',True,0,100)['execution_count']==0
    assert repo.dimensions()['topics']==['/output']


def test_topic_columns_show_subscriptions_and_publish_observations(make_repository):
    repo=make_topics(make_repository)
    data=repo.timeline(Filters(topics=('/output',)),'topic',0,30)
    assert data['lanes']==[{'id':'/output','label':'/output'}]
    assert len(data['records'])==2
    assert {r['callback_type'] for r in data['records']}=={'subscription','timer'}
    publishing=next(r for r in data['records'] if r['callback_type']=='timer')
    assert publishing['start_ns']=='1' and publishing['end_ns']=='20'
    assert publishing['topic_publish_count']==1 and publishing['publishes'][0]['timestamp_ns']=='2'
    assert all(r['lane']=='/output' for r in data['records'])
    assert [p['timestamp_ns'] for p in data['publications']]==['2','21']
    assert data['publications'][0]['execution_id'] is not None
    assert data['publications'][1]['execution_id'] is None
    narrow=repo.timeline(Filters(),'topic',8,18)
    assert len(narrow['records'])==2 and narrow['publications']==[]
    assert next(r for r in narrow['records'] if r['callback_type']=='timer')['publishes']==[]
    assert repo.timeline(Filters(topics=('/missing',)),'topic',0,30)['lanes']==[]
    assert repo.timeline(Filters(none=True),'topic',0,30)['records']==[]


def test_topic_markers_filter_output_topics_without_losing_callback(make_repository):
    repo=make_repository([node(),*timer(),publisher(),
        event('rcl_publisher_init',0,publisher_handle=910,node_handle=100,
              rmw_publisher_handle=911,topic_name='/other',queue_depth=10),
        *execution(1,20),*publish(2),
        event('rcl_publish',5,message=2000,publisher_handle=910)])[0]
    data=repo.timeline(Filters(topics=('/output',)),'node',0,20)
    assert len(data['records'])==1
    assert data['records'][0]['publish_count']==1
    assert [p['topic'] for p in data['records'][0]['publishes']]==['/output']
    assert repo.timeline(Filters(topics=('/output',)),'topic',0,20)['publications'][0]['topic']=='/output'
    selected=Filters(topics=('/output',))
    execution_id=data['records'][0]['id']
    assert repo.execution(execution_id,selected)['publish_count']==1
    details=repo.publications(execution_id,filters=selected)
    assert details['total']==1 and [p['topic'] for p in details['items']]==['/output']
    assert repo.publication(2,selected) is None
    narrow=replace(selected,start=3,end=10)
    assert repo.execution(execution_id,narrow)['publish_count']==0
    assert repo.publications(execution_id,filters=narrow)['items']==[]


def test_service_metadata_is_process_scoped_and_has_real_durations(make_repository):
    repo=service_repository(make_repository)
    data=repo.services(Filters(),service='/add')
    row=data['items'][0]
    assert row['execution_count']==2 and row['total_callback_ns']=='15'
    assert row['mean_duration_ns']==7.5 and row['min_duration_ns']=='5' and row['max_duration_ns']=='10'
    assert row['server_nodes']==['/robot/alpha'] and row['client_nodes']==['/robot/client']
    assert row['server_count']==row['client_count']==1
    assert data['request_response_timing'] is False
    assert repo.catalog('callbacks',search='/second')['items'][0]['node_name']=='/robot/server_b'
    assert repo.locate(Filters(processes=(2,)))['callback_service']=='/other'
    assert repo.dimensions()['services']==['/add','/other','/second']


def test_service_filters_apply_to_all_callback_aggregates(make_repository):
    repo=service_repository(make_repository)
    f=Filters.parse({'services':'/add,/second'})
    assert repo.statistics(f,'process','total_ns',True,0,100)['execution_count']==3
    assert repo.boxplots(f)['execution_count']==3
    assert {r['service'] for r in repo.services(f)['items']}=={'/add','/second'}
    assert repo.services(Filters(services=('/missing',)))['items']==[]
    assert repo.services(Filters(none=True))['items']==[]
    row=repo.services(replace(f,start=4,end=15),service='/add')['items'][0]
    assert row['execution_count']==0 # both server executions cross the interval boundaries


def test_service_timeline_and_density_preserve_overlapping_callbacks(make_repository):
    repo=service_repository(make_repository)
    data=repo.timeline(Filters(),'service',0,30)
    assert len(data['lanes'])==3 and len(data['records'])==4
    assert all(r['callback_type']=='service' for r in data['records'])
    assert data['publications']==[]
    filtered=repo.timeline(Filters(services=('/add',)),'service',4,15)
    assert [r['lane'] for r in filtered['records']]==['/add','/add']
    dense=repo.timeline(Filters(services=('/add',)),'service',0,30,budget=1,bins=32)
    assert dense['mode']=='density' and dense['density']
    assert {r['lane'] for r in dense['density']}=={'/add'}
    assert repo.timeline(Filters(services=('/missing',)),'service',0,30)['records']==[]


def test_publish_density_is_bounded_and_keeps_counts(make_repository):
    events=[node(),publisher(),*subscription(pid=1)]
    events += [event('rcl_publish',i+1,message=1000+i,publisher_handle=900) for i in range(1000)]
    repo=make_repository(events)[0]
    data=repo.timeline(Filters(),'topic',0,1000,budget=10,bins=32)
    assert data['publications_mode']=='density'
    assert len(data['publications'])<=10
    assert sum(p['count'] for p in data['publications'])==1000
    assert all('id' not in p for p in data['publications'])


def test_publishing_callback_appears_once_per_topic_and_same_topic_subscription(make_repository):
    repo=make_repository([node(),*subscription(pid=1),publisher(),
        event('rcl_publisher_init',0,publisher_handle=910,node_handle=100,
              rmw_publisher_handle=911,topic_name='/other',queue_depth=10),
        *execution(1,20,callback=700),*publish(2),*publish(5),
        event('rcl_publish',8,message=2000,publisher_handle=910),
        *execution(30,40,callback=700)])[0]
    data=repo.timeline(Filters(),'topic',0,40)
    assert len(data['records'])==3 # one subscription-only execution plus two topic appearances
    both=next(r for r in data['records'] if r['start_ns']=='1' and r['lane']=='/output')
    other=next(r for r in data['records'] if r['lane']=='/other')
    assert both['topic_subscription']==1 and both['topic_publish_count']==2
    assert len(both['publishes'])==2
    assert other['id']==both['id'] and other['topic_subscription']==0 and other['topic_publish_count']==1
    assert [p['topic'] for p in other['publishes']]==['/other']
    totals=repo.statistics(Filters(topics=('/output','/other')),'callback','total_ns',True,0,100)
    assert totals['execution_count']==2 and totals['total_callback_ns']=='29'
    data=repo.timeline(Filters(topics=('/other',)),'topic',10,15)
    assert len(data['records'])==1 and data['records'][0]['id']==both['id']
    assert data['records'][0]['publishes']==[] # publish occurred before the viewport
    dense=repo.timeline(Filters(),'topic',0,40,budget=1,bins=32)
    assert dense['mode']=='density'
    # The publishing callback contributes its full interval on /other.
    assert any(r['lane']=='/other' and r['bin']>=12 for r in dense['density'])
