import pytest
import re
from fastapi.testclient import TestClient

from ros2_trace_viewer.server import create_app


@pytest.fixture
def client(repository):
    with TestClient(create_app(repository.path)) as client:
        yield client


@pytest.mark.parametrize('path', ['/', '/static/app.js', '/static/timeline.js', '/static/timers.js',
    '/api/trace/info', '/api/dimensions', '/api/processes', '/api/nodes',
    '/api/callbacks', '/api/timeline', '/api/statistics', '/api/charts', '/api/locate', '/api/boxplots', '/api/topics', '/api/services', '/api/timers'])
def test_read_endpoints(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers['x-content-type-options'] == 'nosniff'
    assert response.headers['cache-control'] == 'no-store'


def test_module_assets_share_a_versioned_directory(client):
    html=client.get('/').text
    script=re.search(r'src="(/assets/[0-9a-f]{16}/app.js)"',html)
    assert script
    directory=script.group(1).rsplit('/',1)[0]
    for name in ['app.js','shared.js','statistics.js','boxplots.js','timers.js','style.css']:
        response=client.get(directory+'/'+name)
        assert response.status_code==200
        assert response.headers['cache-control']=='no-store'


def test_execution_and_adjacent_endpoints(client):
    first = client.get('/api/locate').json()
    response = client.get(f'/api/executions/{first["id"]}')
    assert response.json()['duration_ns'] == '20'
    assert response.json()['cpu_intervals'] == []
    next_record = client.get(f'/api/executions/{first["id"]}/next').json()
    assert next_record['start_ns'] == '40'
    assert client.get('/api/executions/99999').status_code == 404


def test_api_filters_and_bounds(client):
    response = client.get('/api/statistics?start_time=0.000000020&end_time=0.000000050')
    assert response.json()['execution_count'] == 1
    assert response.json()['crossing_count'] == 3
    assert client.get('/api/statistics?none=1').json()['rows'] == []
    assert client.get('/api/timeline?processes=2').json()['records'][0]['pid'] == 2
    assert client.get('/api/callbacks?limit=1').json()['total'] == 3
    assert len(client.get('/api/callbacks?limit=1&offset=1').json()['items']) == 1


@pytest.mark.parametrize('url', ['/api/timeline?grouping=cpu', '/api/timeline?grouping=invalid',
    '/api/timeline?view_start=-1', '/api/timeline?view_start=2&view_end=1',
    '/api/timeline?budget=20001', '/api/timeline?start_time=nan', '/api/statistics?sort=invalid',
    '/api/statistics?level=all', '/api/statistics?limit=9999', '/api/callbacks?offset=-1',
    '/api/topics?limit=101', '/api/topics?offset=-1', '/api/services?limit=101', '/api/services?offset=-1', '/api/timers?limit=101', '/api/timers?offset=-1', '/api/timers?sort=invalid', '/api/boxplots?limit=101', '/api/boxplots?offset=-1', '/api/boxplots?sort=invalid',
    '/api/processes?pid=1', '/api/executions/1/bogus'])
def test_invalid_queries_are_clear(client, url):
    response = client.get(url)
    assert response.status_code == 422
    assert 'detail' in response.json()


def test_no_file_access_or_mutations(client):
    assert client.get('/api/not-a-file').status_code == 404
    assert client.get('/static/../../../../etc/passwd').status_code == 404
    assert client.post('/api/trace/info', json={'trace_dir': '/etc'}).status_code == 405
    assert client.get('/api/trace/info').json()['capabilities']['cpu_execution'] is False


def test_boxplots_endpoint_filters_and_bounded_page(client):
    result = client.get('/api/boxplots?sort=total_ns&limit=1').json()
    assert result['total_groups'] == 3 and len(result['rows']) == 1
    assert result['axis_max_ns'] == '40'
    assert result['rows'][0]['q1_ns'] == 25
    assert result['rows'][0]['q3_ns'] == 35
    assert result['rows'][0]['outlier_count'] == 0
    assert client.get('/api/boxplots?none=1').json()['rows'] == []
    result = client.get('/api/boxplots?start_time=0.000000020&end_time=0.000000050').json()
    assert result['execution_count'] == 1 and result['crossing_count'] == 3
    assert client.post('/api/boxplots').status_code == 405


def test_publication_endpoints_and_topic_lookup(client):
    first=client.get('/api/locate').json()
    data=client.get(f'/api/executions/{first["id"]}/publications').json()
    assert data['total']==1 and data['items'][0]['topic']=='/output'
    assert data['items'][0]['node_name']=='/robot/alpha'
    pub=client.get('/api/publications/1').json()
    assert pub['execution_id']==first['id']
    assert client.get('/api/publications/99999').status_code==404
    assert client.get('/api/executions/99999/publications').status_code==404
    assert client.get('/api/executions/1/publications?limit=101').status_code==422
    assert client.get('/api/topics?topic=/output').json()['items'][0]['publish_count']==1
    assert client.post('/api/topics').status_code==405


def test_resource_filters_and_timelines(client):
    result=client.get('/api/timeline?grouping=topic&topics=/output').json()
    assert result['lanes'][0]['id']=='/output'
    assert len(result['publications'])==1
    assert client.get('/api/timeline?grouping=service').json()['lanes']==[]
    assert client.get('/api/statistics?topics=/output').json()['execution_count']==1
    assert client.get('/api/statistics?services=/missing').json()['execution_count']==4
    assert client.get('/api/services').json()['request_response_timing'] is False
    assert client.post('/api/services').status_code==405


def test_publish_details_respect_applied_topic_and_time_filters(client):
    record=client.get('/api/locate').json()
    identifier=record['id']
    result=client.get(f'/api/executions/{identifier}/publications?topics=/hidden').json()
    assert result['total']==0 and result['items']==[]
    assert client.get(f'/api/executions/{identifier}?topics=/hidden').json()['publish_count']==0
    assert client.get('/api/publications/1?topics=/hidden').status_code==404
    result=client.get(f'/api/executions/{identifier}/publications?start_time=0.000000015').json()
    assert result['total']==0 and result['items']==[]
    assert client.get(f'/api/executions/{identifier}/publications?topics=/output').json()['total']==1
