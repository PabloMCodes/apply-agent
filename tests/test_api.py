from importlib import import_module

from fastapi.testclient import TestClient
import pytest

from src.api.app import create_app
from src.jobs.fetch import FetchError

api_module = import_module('src.api.app')
TABLE = '''| Company | Position | Location | Salary | Posting | Age |
|---|---|---|---|---|---|
| <strong>Example</strong> | SWE Intern | NYC<br>Remote | $50/hr | <a href="https://example.com/job/1">Apply</a> | 1d |
'''
JOB = dict(company='Example', title='Engineer', location='Remote', application_url='https://example.com/job/1')


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / 'test.sqlite3')) as client:
        yield client


def test_job_lifecycle(client):
    assert client.get('/health').json() == {'status': 'ok'}
    created = client.post('/jobs', json=JOB)
    assert created.status_code == 201
    job_id = created.json()['id']
    assert client.post('/jobs', json=JOB).status_code == 409
    assert client.get(f'/jobs/{job_id}').json()['company'] == 'Example'
    assert client.get('/jobs?q=remote').json()['total'] == 1
    assert client.get('/jobs?q=missing').json()['total'] == 0
    assert client.get('/jobs?offset=1').json()['items'] == []
    tracked = client.put(f'/jobs/{job_id}/tracking', json={'status': 'saved', 'notes': 'Review later'})
    assert tracked.json()['notes'] == 'Review later'
    assert client.get('/jobs?status=saved').json()['total'] == 1
    assert client.delete(f'/jobs/{job_id}').status_code == 204
    assert client.get(f'/jobs/{job_id}').status_code == 404
    assert client.delete(f'/jobs/{job_id}').status_code == 404
    assert client.put(f'/jobs/{job_id}/tracking', json={'status': 'new'}).status_code == 404


def test_validation(client):
    assert client.post('/jobs', json={**JOB, 'company': '  '}).status_code == 422
    assert client.post('/jobs', json={**JOB, 'application_url': 'javascript:alert(1)'}).status_code == 422
    assert client.get('/jobs?limit=0').status_code == 422
    assert client.get('/jobs?offset=-1').status_code == 422
    assert client.get('/jobs?status=bogus').status_code == 422
    assert client.post('/ingestions', json={'source': 'http://localhost'}).status_code == 422
    assert client.put('/profile', json={'unknown': 'value'}).status_code == 422


def test_ingestion_is_repeatable_and_preserves_tracking(client, monkeypatch):
    monkeypatch.setattr(api_module, 'fetch_jobs', lambda url: TABLE)
    assert len(client.get('/sources').json()) == 4
    first = client.post('/ingestions', json={})
    assert first.status_code == 201
    assert first.json()['inserted'] == 1
    job_id = client.get('/jobs').json()['items'][0]['id']
    client.put(f'/jobs/{job_id}/tracking', json={'status': 'saved', 'notes': 'Keep'})
    second = client.post('/ingestions', json={}).json()
    assert second['inserted'] == 0 and second['updated'] == 1
    assert client.get('/jobs').json()['total'] == 1
    assert client.get(f'/jobs/{job_id}').json()['notes'] == 'Keep'
    assert client.get('/ingestions/1').json()['found'] == 1
    assert len(client.get('/ingestions').json()) == 2
    assert client.get('/ingestions/999').status_code == 404


def test_failed_ingestion_writes_nothing(client, monkeypatch):
    def fail(url):
        raise FetchError('Unavailable')
    monkeypatch.setattr(api_module, 'fetch_jobs', fail)
    assert client.post('/ingestions', json={}).status_code == 502
    monkeypatch.setattr(api_module, 'fetch_jobs', lambda url: 'Unexpected format')
    assert client.post('/ingestions', json={}).status_code == 502
    assert client.get('/jobs').json()['total'] == 0
    assert client.get('/ingestions').json() == []


def test_profile_and_matching_contract(client):
    assert client.get('/profile').status_code == 404
    saved = client.put('/profile', json={'name': 'Alex', 'skills': ['Python'], 'resume_text': 'Experience'})
    assert saved.status_code == 200
    assert client.get('/profile').json()['skills'] == ['Python']
    client.put('/profile', json={'name': 'New name'})
    assert client.get('/profile').json()['skills'] == []
    assert client.post('/matches', json={'job_ids': [1]}).status_code == 501
    assert client.post('/matches', json={'job_ids': []}).status_code == 422
    assert client.delete('/profile').status_code == 204
    assert client.get('/profile').status_code == 404
    schema = client.get('/openapi.json').json()
    assert '501' in schema['paths']['/matches']['post']['responses']


def test_data_survives_app_restart(tmp_path):
    path = tmp_path / 'persist.sqlite3'
    with TestClient(create_app(path)) as client:
        client.post('/jobs', json=JOB)
        client.put('/profile', json={'name': 'Alex'})
    with TestClient(create_app(path)) as client:
        assert client.get('/jobs').json()['total'] == 1
        assert client.get('/profile').json()['name'] == 'Alex'
