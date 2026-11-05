"""Explicit action metadata and call scopes, including measured blocking waits."""
from collections import Counter,defaultdict
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from conftest import event,node,execution,timer
from ros2_trace_viewer.trace.filters import Filters
from ros2_trace_viewer.trace.loader import load_trace,read_events
from ros2_trace_viewer.trace.repository import Repository
from ros2_trace_viewer.server import create_app

ACTION='/demo/fibonacci'


def action_init(pid=1,callback=700,owner=100,role='execute'):
    return event('ros2_trace_viewer:action_callback_init',0,pid=pid,callback=callback,
        node_handle=owner,action_name=ACTION,role=role,side='server',symbol='Demo::'+role)


def call(t=3,pid=1,identity=1,service='/add',waits=True):
    return event('ros2_trace_viewer:client_call',t,pid=pid,call_id=identity,node_handle=100,
                 service_name=service,request_id=9,wait_for_response=int(waits))


def test_action_callback_metadata_is_explicit_and_process_scoped(make_repository):
    repo=make_repository([node(),node(pid=2,name='other'),action_init(),action_init(pid=2),
                          *execution(1,10,callback=700),*execution(2,8,callback=700,pid=2)])[0]
    rows=repo.statistics(Filters(types=('action',)),'callback','name',False,0,100)['rows']
    assert len(rows)==2 and {row['node_name'] for row in rows}=={'/robot/alpha','/robot/other'}
    assert {row['callback_action'] for row in rows}=={ACTION}
    assert all(row['action_role']=='execute' and row['action_side']=='server' for row in rows)
    assert repo.info['capabilities']['action_callback_timing']
    assert repo.boxplots(Filters(types=('action',)))['total_groups']==2
    assert repo.timeline(Filters(),'action',0,10)['records']


def test_nested_calls_waits_and_scope_flush_are_matched(make_repository):
    repo=make_repository([node(),*timer(),action_init(),*execution(1,20),
        *execution(2,12,callback=700),call(),event('ros2_trace_viewer:client_call_end',8,call_id=1),
        call(15,identity=2,waits=False),call(21,identity=3,waits=True)])[0]
    with repo.connect() as db:rows=[dict(row) for row in db.execute('SELECT * FROM client_calls ORDER BY id')]
    first=repo.client_call(rows[0]['id'],Filters())
    assert first['wait_duration_ns']=='5' and first['execution']['callback_type']=='action'
    second=repo.client_call(rows[1]['id'],Filters())
    assert second['execution']['callback_type']=='timer' and not second['wait_for_response']
    third=repo.client_call(rows[2]['id'],Filters())
    assert third['execution'] is None and third['wait_end_ns'] is None
    assert repo.client_calls(first['execution_id'],Filters())['total']==1
    assert repo.client_call(rows[0]['id'],Filters(services=('/different',))) is None
    assert repo.timeline(Filters(),'node',0,20)['records'][0]['calls']


def test_action_token_reuse_does_not_relabel_a_later_timer(make_repository):
    repo=make_repository([node(),action_init(),*execution(1,4,callback=700),
        *timer(callback=700,t=5),*execution(6,9,callback=700)])[0]
    data=repo.statistics(Filters(),'callback','name',False,0,100)
    assert {row['type'] for row in data['rows']}=={'action','timer'}
    assert len({row['id'] for row in data['rows']})==2


def test_incomplete_callback_does_not_gain_a_call_duration(make_repository):
    repo=make_repository([node(),*timer(),event('callback_start',1,callback=500),call()])[0]
    result=repo.client_call(1,Filters())
    assert result['execution'] is None and result['wait_end_ns'] is None


def test_many_calls_keep_counts_and_page_without_unbounded_markers(make_repository):
    events=[node(),*timer(),*execution(1,12000)]
    events += [call(t=3+index,identity=index+1,waits=False) for index in range(10500)]
    repo=make_repository(events)[0]
    row=repo.timeline(Filters(),'node',0,12000)['records'][0]
    assert row['call_count']==10500 and len(row['calls'])<=128
    assert sum(point['count'] for point in row['calls'])==10500
    assert repo.client_calls(row['id'],Filters(),offset=10495,limit=20)['total']==10500
    assert len(repo.client_calls(row['id'],Filters(),offset=10495,limit=20)['items'])==5


@pytest.mark.integration
def test_genuine_full_demo(tmp_path):
    configured=os.environ.get('ROS2_TRACE_VIEWER_TEST_FULL_TRACE')
    if not configured:pytest.skip('Set ROS2_TRACE_VIEWER_TEST_FULL_TRACE to the instrumented CTF demo')
    source=Path(configured)
    before={str(p.relative_to(source)):(p.stat().st_size,p.stat().st_mtime_ns) for p in source.rglob('*') if p.is_file()}
    registered={};starts=defaultdict(list);expected=Counter();totals=Counter();calls=waits=0
    for record in read_events(source):
        pid=record.get('vpid');pointer=record.get('callback');name=record['_name']
        if name=='ros2_trace_viewer:action_callback_init':registered[pid,pointer]=record['role']
        key=(pid,record.get('vtid'),pointer)
        if (pid,pointer) in registered:
            if name=='ros2:callback_start':starts[key].append(record['_timestamp'])
            elif name=='ros2:callback_end' and starts[key]:
                role=registered[pid,pointer];expected[role]+=1
                totals[role]+=record['_timestamp']-starts[key].pop()
        if name=='ros2_trace_viewer:client_call':calls+=1;waits+=bool(record['wait_for_response'])
    assert set(expected)=={'goal','cancel','accepted','execute','goal_response','feedback','result'}
    with load_trace(source,progress=lambda _:None,work_dir=tmp_path) as path:
        repo=Repository(path)
        data=repo.boxplots(Filters(types=('action',)),limit=100)
        assert data['total_groups']==7 and data['execution_count']==sum(expected.values())
        for row in data['rows']:
            assert row['count']==expected[row['action_role']]
            assert int(row['total_ns'])==totals[row['action_role']]
            assert row['node_name'] and row['callback_action']=='/viewer_demo/fibonacci'
        active=Filters(exclude_default_services=True,exclude_parameter_events=True)
        timeline=repo.timeline(active,'node',0,int(repo.info['duration_ns']),budget=20000)
        markers=[call for row in timeline['records'] for call in row['calls']]
        assert len(markers)==calls==19
        assert {marker['kind'] for marker in markers}=={'service','action'}
        assert sum(marker['wait_end_ns'] is not None for marker in markers)==waits==4
        for marker in markers:
            execution=repo.execution(marker['execution_id'])
            assert int(execution['start_ns'])<=int(marker['timestamp_ns'])<=int(execution['end_ns'])
            if marker['wait_end_ns'] is not None:
                assert int(marker['wait_duration_ns'])>20_000_000
                assert int(marker['wait_end_ns'])<=int(execution['end_ns'])
        services=repo.services(active)['items']
        row=next(row for row in services if row['service']=='/viewer_demo/add_two_ints')
        assert row['call_count']==row['execution_count']==7 and row['measured_wait_count']==4
        hidden_actions=Filters(actions=('__none__',),exclude_default_services=True)
        assert repo.services(hidden_actions)['items'][0]['call_count']==7
        hidden_timeline=repo.timeline(hidden_actions,'service',0,int(repo.info['duration_ns']))
        assert {row['callback_type'] for row in hidden_timeline['records']}=={'timer','service'}
        assert all(call['kind']=='service' for row in hidden_timeline['records'] for call in row['calls'])
        service_timeline=repo.timeline(active,'service',0,int(repo.info['duration_ns']))
        assert {row['callback_type'] for row in service_timeline['records']}=={'timer','service'}
        with TestClient(create_app(path)) as client:
            response=client.get('/api/calls/1');assert response.status_code==200
            assert response.json()['execution'] is not None
            assert client.get('/api/calls/1?none=1').status_code==404
            assert client.get('/api/statistics?types=action&level=callback').json()['total_groups']==7
    after={str(p.relative_to(source)):(p.stat().st_size,p.stat().st_mtime_ns) for p in source.rglob('*') if p.is_file()}
    assert before==after
