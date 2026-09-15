import time
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.applications import store as apps
from src.setup import store
from src.jobs.sources import classify, load_source
from src.jobs.models import Job
from src.runtime import pair_update


@pytest.fixture
def workspace(tmp_path):
    path = tmp_path / 'jobs.sqlite3'
    with TestClient(create_app(path)) as client:
        yield client, path


def test_profile_resume_and_preferences(workspace):
    client, path = workspace
    assert client.get('/').status_code == 200
    assert 'apply agent' in client.get('/').text
    assert client.put('/profile', json={'first_name': 'Jo', 'last_name': 'Lee', 'email': 'jo@example.com'}).status_code == 200
    upload = client.post('/resume', files={'file': ('resume.txt', b'Experience in design', 'text/plain')})
    assert upload.status_code == 200
    assert upload.json()['text'] == 'Experience in design'
    assert client.get('/resume/file').content == b'Experience in design'
    assert client.get('/profile').json()['first_name'] == 'Jo'
    assert client.post('/resume', files={'file': ('evil.html', b'<html>')}).status_code == 422
    assert client.post('/resume', files={'file': ('bad.pdf', b'not a pdf')}).status_code == 422
    assert client.put('/settings', json={'roles': ['Designer'], 'locations': ['Remote'], 'monitoring_enabled': True}).status_code == 200
    assert store.matches_preferences(Job('A','UX Designer','Remote','url','source'), store.preferences(path))
    assert not store.matches_preferences(Job('A','Accountant','Remote','url','source'), store.preferences(path))
    assert client.put('/settings', json={'poll_minutes': 0}).status_code == 422
    assert client.delete('/resume').status_code == 204
    assert client.get('/resume/file').status_code == 404


def test_sources_are_explicit_about_support(workspace, monkeypatch):
    client, path = workspace
    supported = client.post('/settings/sources', json={'name':'Acme','url':'https://job-boards.greenhouse.io/acme'})
    assert supported.status_code == 201
    assert supported.json()['kind'] == 'greenhouse_board'
    assert client.post('/settings/sources', json={'name':'Again','url':'https://job-boards.greenhouse.io/acme'}).status_code == 409
    bookmark = client.post('/settings/sources', json={'name':'Other','url':'https://example.com/careers'}).json()
    assert bookmark['kind'] == 'bookmark'
    assert bookmark['enabled'] == 0
    assert client.patch(f'/settings/sources/{bookmark["id"]}', json={'enabled':True}).status_code == 422
    assert client.post(f'/settings/sources/{bookmark["id"]}/check').status_code == 422
    assert classify('https://job-boards.greenhouse.io.evil.test/acme') == 'bookmark'
    assert client.post('/settings/sources', json={'name':'Bad','url':'https://user:pass@example.com'}).status_code == 422


def test_greenhouse_imports_non_software_jobs(monkeypatch):
    monkeypatch.setattr('src.jobs.sources.get_json', lambda url: {'jobs':[{'id':12,'title':'Registered Nurse','location':{'name':'Chicago'},'absolute_url':'https://example.com/custom'}]})
    jobs = load_source({'name':'Hospital','url':'https://job-boards.greenhouse.io/hospital'})
    assert jobs[0].title == 'Registered Nurse'
    assert jobs[0].application_url == 'https://job-boards.greenhouse.io/hospital/jobs/12'


def test_pairing_requires_expiring_secret_and_redacts_token(workspace, monkeypatch):
    client, path = workspace
    fake = Mock()
    fake.call.side_effect = [{'username':'my_bot'}, {'url':''}]
    monkeypatch.setattr('src.setup.routes.TelegramClient', lambda token: fake)
    response = client.post('/settings/telegram/connect', json={'token':'123456:secret-token'})
    assert response.status_code == 200
    assert 'secret-token' not in response.text
    assert 'secret-token' not in client.get('/settings/telegram').text
    config = store.get(path, 'telegram')
    update = {'update_id':1,'message':{'from':{'id':42},'chat':{'id':42,'type':'private'},'text':'/start wrong'}}
    assert not pair_update(path, config, update)
    update['message']['text'] = '/start ' + config['pairing_code']
    expired = {**config, 'pairing_expires':time.time()-1}
    assert not pair_update(path, expired, update)
    assert pair_update(path, config, update)
    assert client.get('/settings/telegram').json()['connected']
    assert not pair_update(path, config, update)
    client.delete('/settings/telegram')
    assert not client.get('/settings/telegram').json()['configured']


def test_cross_origin_mutations_and_untrusted_hosts_rejected(workspace):
    client, path = workspace
    assert client.put('/settings',json={},headers={'Origin':'https://evil.test'}).status_code == 403
    assert client.put('/settings',json={},headers={'Sec-Fetch-Site':'cross-site'}).status_code == 403
    assert client.get('/profile',headers={'Host':'evil.test'}).status_code == 400
    assert client.put('/settings',json={},headers={'Origin':'http://testserver'}).status_code == 200


def test_application_revision_and_command_dedup(workspace):
    client, path = workspace
    client.put('/profile',json={'first_name':'Jo','last_name':'Lee','email':'jo@example.com'})
    job = client.post('/jobs',json={'company':'Acme','title':'Designer','location':'Remote','application_url':'https://job-boards.greenhouse.io/acme/jobs/1'}).json()
    response = client.post(f'/jobs/{job["id"]}/prepare')
    assert response.status_code == 202
    app_id = response.json()['id']
    assert client.post(f'/jobs/{job["id"]}/prepare').json()['id'] == app_id
    apps.set_run(path, app_id, 'ready', {'fields':[],'can_submit':True,'fingerprint':'test'})
    revision = apps.get(path, app_id)['revision']
    assert client.post(f'/applications/{app_id}/submit',json={'revision':revision+1}).status_code == 409
    assert client.post(f'/applications/{app_id}/edit',json={'revision':revision,'field_id':'0:f1','value':True}).status_code == 202
    assert client.post(f'/applications/{app_id}/submit',json={'revision':revision}).status_code == 409
    apps.complete_command(path, apps.next_command(path)['id'])
    assert client.post(f'/applications/{app_id}/submit',json={'revision':revision}).status_code == 202
    apps.set_run(path, app_id, 'submitting')
    apps.recover(path)
    assert apps.get(path,app_id)['status'] == 'submission_unknown'
    assert apps.next_command(path) is None
    assert client.post(f'/applications/{app_id}/retry').status_code == 409


def test_previous_data_survives_new_initializers(tmp_path):
    from src.db import database as db
    from src.telegram import store as telegram
    path = tmp_path / 'legacy.sqlite3'
    db.initialize_database(path)
    telegram.initialize(path)
    db.save_jobs([Job('Acme','Nurse','Remote','https://example.com/job','manual')],path)
    with db.connect(path) as conn:
        conn.execute("INSERT INTO application_queue(application_url) VALUES ('https://example.com/job')")
    with TestClient(create_app(path)) as client:
        assert client.get('/jobs').json()['total'] == 1
        assert client.get('/applications').json()[0]['status'] == 'queued'


def test_integrated_services_start_without_telegram_credentials(tmp_path):
    with TestClient(create_app(tmp_path/'runtime.sqlite3',start_workers=True)) as client:
        assert client.get('/health').status_code==200
        assert not client.get('/settings/telegram').json()['configured']
        assert not client.get('/settings').json()['monitoring_enabled']


def test_browser_network_rejects_private_addresses(monkeypatch):
    from src.applications.network import public_host, public_request
    import socket
    public_host.cache_clear()
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))])
    assert not public_request('https://local-alias.example/path')
    assert not public_request('http://example.com')
    assert not public_request('https://example.com:8000')
    public_host.cache_clear()


def test_careerpuck_import_preserves_original_url(monkeypatch):
    from src.jobs.sources import greenhouse_parts
    url='https://app.careerpuck.com/job-board/lyft/job/8797837002?gh_jid=8797837002'
    assert classify(url)=='careerpuck_job'
    assert greenhouse_parts('https://job-boards.greenhouse.io/embed/job_app?for=lyft&token=8797837002')==('lyft','8797837002')
    assert greenhouse_parts('https://job-boards.greenhouse.io/embed/job_app?for=../../private&token=1') is None
    monkeypatch.setattr('src.jobs.sources.get_json',lambda url:{'id':8797837002,'company_name':'Lyft','title':'Intern','location':{'name':'New York'}})
    result=load_source({'name':'Lyft','url':url})
    assert result[0].application_url==url
    assert result[0].company=='Lyft'


def test_arbitrary_https_job_can_request_form_detection(workspace):
    client,path=workspace
    client.put('/profile',json={'first_name':'Jo','last_name':'Lee','email':'jo@example.com'})
    job=client.post('/jobs',json={'company':'Other','title':'Nurse','location':'Remote','application_url':'https://careers.example.com/apply/1'}).json()
    assert client.post(f'/jobs/{job["id"]}/prepare').status_code==202


def test_prepare_five_jobs_and_partial_errors(workspace):
    client, path = workspace
    client.put('/profile', json={'first_name':'Jo','last_name':'Lee','email':'jo@example.com'})
    ids = [client.post('/jobs', json={'company':'Acme','title':f'Role {i}','location':'Remote',
            'application_url':f'https://example.com/jobs/{i}'}).json()['id'] for i in range(5)]
    result = client.post('/applications/prepare-batch', json={'job_ids':ids})
    assert result.status_code == 202
    assert len(result.json()['items']) == 5
    assert all(i['application']['status'] == 'queued' for i in result.json()['items'])
    assert apps.next_queued(path) == result.json()['items'][0]['application']['id']
    assert client.post('/applications/prepare-batch', json={'job_ids':list(range(501))}).status_code == 422
    assert 'error' in client.post('/applications/prepare-batch', json={'job_ids':[999]}).json()['items'][0]
    client.post('/applications/prepare-batch', json={'job_ids':ids})
    assert len(apps.list_runs(path)) == 5


def test_saved_answer_scope_matching_and_forget(workspace):
    from src.applications import answers
    client, path = workspace
    field = {'id':'0:f1','label':'Portfolio description *','type':'textarea','value':'My work', 'supported':True}
    answers.remember(path, field, 'Acme', 'company')
    fake = Mock()
    fake.snapshot.return_value = {'fields':[dict(field, value='')]}
    assert answers.apply(path, fake, 'Other') == {}
    assert answers.apply(path, fake, 'ACME') == {'0:f1':1}
    fake.edit.assert_called_with('0:f1', 'My work')
    fake.snapshot.return_value = {'fields':[dict(field, value='', label='Different question')]}
    assert answers.apply(path, fake, 'Acme') == {}
    fake.snapshot.return_value = {'fields':[dict(field, value=''),dict(field, id='0:f2',value='')]}
    assert answers.apply(path, fake, 'Acme') == {}
    assert client.get('/saved-answers').json()[0]['value'] == 'My work'
    assert client.delete('/saved-answers/1').status_code == 204
    assert client.get('/saved-answers').json() == []


def test_saved_select_matches_label_not_provider_value(workspace):
    from src.applications import answers
    _, path = workspace
    field = {'id':'0:f1','label':'Arrangement','type':'select','value':'1','supported':True,
             'options':[{'label':'Remote','value':'1'}]}
    answers.remember(path, field, 'Acme', 'all')
    fake = Mock()
    fake.snapshot.return_value = {'fields':[dict(field,value='',options=[{'label':'Remote','value':'99'}])]}
    assert answers.apply(path, fake, 'Other')
    fake.edit.assert_called_once_with('0:f1','99')
    fake.snapshot.return_value = {'fields':[dict(field,value='',options=[{'label':'Onsite','value':'1'}])]}
    assert answers.apply(path, fake, 'Other') == {}


def test_applied_history_survives_closed_tab_import_and_restart(workspace):
    from src.db import database as db
    client, path = workspace
    job = client.post('/jobs', json={'company':'Acme','title':'Engineer','location':'Remote','application_url':'https://example.com/jobs/track'}).json()
    app_id = apps.queue_job(path, job['id'])
    apps.set_run(path, app_id, 'needs_attention', message='Browser closed')
    assert client.get(f'/jobs/{job["id"]}').json()['applied_at'] is None
    marked = client.put(f'/jobs/{job["id"]}/tracking', json={'status':'applied','notes':'Confirmed on employer site'}).json()
    assert marked['applied_at']
    assert client.get(f'/applications/{app_id}').json()['status'] == 'submitted'
    assert client.get('/applications?active_only=true').json() == []
    assert client.get('/jobs?status=applied&q=Acme').json()['total'] == 1
    with pytest.raises(ValueError, match='applied'):
        apps.queue_job(path, job['id'])
    db.save_jobs([Job('Acme','Engineer','Remote',job['application_url'],'test')],path)
    apps.set_run(path, app_id, 'ready')  # A late worker update cannot erase history.
    db.initialize_database(path)
    again = client.get(f'/jobs/{job["id"]}').json()
    assert again['status'] == 'applied'
    assert again['applied_at'] == marked['applied_at']
    assert again['notes'] == marked['notes']
    client.put(f'/jobs/{job["id"]}/tracking', json={'status':'new'})
    assert client.get(f'/jobs/{job["id"]}').json()['applied_at'] is None
    assert client.get(f'/applications/{app_id}').json()['status'] == 'cancelled'
    assert apps.queue_job(path, job['id']) == app_id
    assert apps.get(path, app_id)['status'] == 'queued'


def test_only_confirmed_submission_sets_applied_date(workspace):
    client, path = workspace
    job = client.post('/jobs', json={'company':'Acme','title':'Engineer','location':'Remote','application_url':'https://example.com/jobs/confirm'}).json()
    app_id = apps.queue_job(path, job['id'])
    apps.set_run(path, app_id, 'submission_unknown')
    assert client.get('/jobs?status=applied').json()['total'] == 0
    apps.set_run(path, app_id, 'submitted')
    first = client.get(f'/jobs/{job["id"]}').json()
    assert first['applied_at']
    apps.set_run(path, app_id, 'submitted')
    assert client.get(f'/jobs/{job["id"]}').json()['applied_at'] == first['applied_at']


def test_legacy_applied_dates_remain_unknown(tmp_path):
    import sqlite3
    from src.db import database as db
    path = tmp_path / 'old.sqlite3'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE jobs (id INTEGER PRIMARY KEY, status TEXT)')
        conn.execute("INSERT INTO jobs VALUES (1,'applied')")
    db.initialize_database(path)
    with db.connect(path) as conn:
        assert conn.execute('SELECT applied_at FROM jobs').fetchone()[0] is None
