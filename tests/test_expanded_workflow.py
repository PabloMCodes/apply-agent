"""Local data and optional drafting contracts; no real model or employer calls."""
import json
from unittest.mock import Mock
import pytest
from fastapi.testclient import TestClient
from src.api.app import create_app
from src.applications import store as apps
from src.applications import questions
from src.setup import resume, store
from src.ai import provider


@pytest.fixture
def workspace(tmp_path):
    path=tmp_path/'jobs.sqlite3'
    with TestClient(create_app(path)) as client:
        yield client,path


def test_multiple_resumes_migration_titles_and_explicit_choice(workspace):
    client,path=workspace
    client.post('/resume',files={'file':('old.txt',b'Old experience','text/plain')})
    assert len(client.get('/resumes').json())==1
    assert len(client.get('/resumes').json())==1
    def upload(title):
        return client.post('/resumes',data={'title':title},files={'file':('new.txt',b'New experience','text/plain')})
    assert upload(' ').status_code==422
    designer=upload('Product designer').json()
    nurse=upload('Registered nurse').json()
    assert len(client.get('/resumes').json())==3
    assert resume.choose(path,1,'Product designer')[0]['id']==designer['id']
    with pytest.raises(ValueError,match='Choose a resume'):
        resume.choose(path,1,'Accountant')
    client.put('/profile',json={'first_name':'Jo','last_name':'Lee','email':'jo@example.com'})
    job=client.post('/jobs',json={'company':'Acme','title':'Role','location':'Remote','application_url':'https://example.com/apply'}).json()
    result=client.post('/applications/prepare-batch',json={'job_ids':[job['id']],'resume_id':nurse['id']}).json()['items'][0]['application']
    assert resume.choose(path,result['id'],'Product designer')[0]['id']==nurse['id']
    client.delete(f"/resumes/{nurse['id']}")
    with pytest.raises(ValueError,match='removed'):
        resume.choose(path,result['id'],'Product designer')
    assert client.get(f"/resumes/{designer['id']}/file").content==b'New experience'


def test_hundreds_queue_and_preserve_tracking(workspace):
    client,path=workspace
    client.put('/profile',json={'first_name':'Jo','last_name':'Lee','email':'jo@example.com'})
    jobs=[{'company':'Acme','title':f'Role {i}','location':'Remote','application_url':f'https://example.com/jobs/{i}'} for i in range(205)]
    assert client.post('/jobs/bulk',json={'jobs':jobs}).status_code==201
    ids=[j['id'] for j in client.get('/jobs?limit=200').json()['items']]
    ids += [j['id'] for j in client.get('/jobs?limit=200&offset=200').json()['items']]
    result=client.post('/applications/prepare-batch',json={'job_ids':ids})
    assert result.status_code==202
    assert len(result.json()['items'])==205
    assert len(client.get('/applications?limit=200').json())==200
    assert len(client.get('/applications?limit=200&offset=200').json())==5
    first=result.json()['items'][0]['application']['id']
    apps.set_run(path,first,'ready',{'fields':[]})
    assert apps.next_queued(path)!=first
    assert client.put('/settings',json={'review_slots':12}).status_code==200
    assert client.put('/settings',json={'review_slots':0}).status_code==422


def test_context_variants_conflicts_and_demographics(workspace):
    client,_=workspace
    fact={'id':'f1','question':'Tell us about your projects','answer':'Built a library tool','variants':['Describe your projects'],'context':''}
    profile={'experience':'Long career history','facts':[fact]}
    assert client.put('/profile',json=profile).status_code==200
    saved=client.get('/profile').json()
    assert questions.match(saved,'Describe your projects')['status']=='new_wording'
    assert questions.match(saved,'Tell us about your projects')['status']=='previously_confirmed'
    assert questions.match(saved,'Why this employer?') is None
    assert questions.match({'facts':[fact,dict(fact,id='f2',answer='Conflicting answer')]},fact['question']) is None
    contextual=dict(fact,context='Canada')
    assert questions.match({'facts':[contextual]},fact['question']) is None
    identity=dict(fact,question='Are you Hispanic?',variants=[],answer='Yes',sensitive=True)
    assert questions.match({'facts':[identity]},'Are you Hispanic?')['suggest_only']
    assert questions.match({'facts':[identity]},'Race') is None


def test_ai_disabled_redaction_and_grounded_contract(workspace,monkeypatch):
    client,path=workspace
    field={'label':'Describe your experience','type':'textarea','options':[]}
    with pytest.raises(ValueError,match='Configure'):
        provider.suggest(path,{},field)
    client.put('/settings/ai',json={'enabled':True,'base_url':'http://localhost:1234/v1','model':'local','api_key':'secret'})
    assert 'secret' not in client.get('/settings/ai').text
    response=Mock()
    response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
    response.iter_bytes.return_value=[json.dumps({'choices':[{'message':{'content':json.dumps({'answer':'Built tools.','sources':['experience']})}}]}).encode()]
    model_client=Mock()
    model_client.__enter__=Mock(return_value=model_client);model_client.__exit__=Mock(return_value=False)
    model_client.stream.return_value=response
    monkeypatch.setattr(provider.httpx,'Client',lambda **kwargs:model_client)
    profile={'experience':'Built tools.','email':'private@example.com','facts':[{'id':'secret','question':'Private fact','answer':'hidden','share_with_ai':False}]}
    draft=provider.suggest(path,profile,field)
    assert draft['pending'] and draft['status']=='ai_draft'
    request=model_client.stream.call_args.kwargs['json']
    assert 'private@example.com' not in json.dumps(request)
    assert 'hidden' not in json.dumps(request)
    with pytest.raises(ValueError,match='identity'):
        provider.suggest(path,profile,dict(field,label='Race / ethnicity'))
    response.iter_bytes.return_value=[json.dumps({'choices':[{'message':{'content':'{"answer":"invented","sources":["missing"]}'}}]}).encode()]
    with pytest.raises(ValueError,match='references'):
        provider.suggest(path,profile,field)
    client.put('/settings/ai',json={'enabled':True,'base_url':'https://other.example/v1','model':'another'})
    assert not client.get('/settings/ai').json()['has_key']


def test_retry_selection_requeues_expired_but_not_unknown_submission(workspace):
    client,path=workspace
    client.put('/profile',json={'first_name':'Jo','last_name':'Lee','email':'jo@example.com'})
    job=client.post('/jobs',json={'company':'Acme','title':'Role','location':'Remote','application_url':'https://example.com/apply'}).json()
    run=client.post(f"/jobs/{job['id']}/prepare").json()
    apps.set_run(path,run['id'],'expired')
    assert client.post(f"/jobs/{job['id']}/prepare").json()['status']=='queued'
    apps.set_run(path,run['id'],'submission_unknown')
    assert client.post(f"/jobs/{job['id']}/prepare").json()['status']=='submission_unknown'


def test_conflicting_memory_and_profile_are_left_blank(workspace):
    from src.applications import answers
    _,path=workspace
    field={'id':'0:f1','label':'Availability','type':'text','value':'Tomorrow','supported':True}
    answers.remember(path,field,'Acme','company')
    session=Mock()
    session.field_reviews={}
    session.snapshot.return_value={'fields':[dict(field,value='')]}
    profile={'facts':[{'id':'a','question':'Availability','answer':'Next month'}]}
    questions.flag_conflicts(path,profile,session,'Acme')
    assert session.field_reviews['0:f1']['conflict']
    assert answers.apply(path,session,'Acme')=={}
    questions.apply(profile,session)
    session.edit.assert_not_called()
