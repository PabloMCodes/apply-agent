import json
from unittest.mock import Mock
import pytest
from src.ai import browser_plan
from src.api.app import create_app
from fastapi.testclient import TestClient
from src.setup import store


@pytest.fixture
def plan_workspace(tmp_path,monkeypatch):
    path=tmp_path/'jobs.sqlite3'
    with TestClient(create_app(path)):
        store.put(path,'ai',{'enabled':True,'base_url':'https://model.example/v1','model':'test'})
        session=Mock();session.page.url='https://careers.example.com/apply';session.page.frames=[]
        session.snapshot.return_value={'fields':[{'id':'0:f1','label':'Your given name','type':'text','options':[],'value':'','supported':True}]}
        response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        client=Mock();client.__enter__=Mock(return_value=client);client.__exit__=Mock(return_value=False);client.stream.return_value=response
        monkeypatch.setattr(browser_plan.httpx,'Client',lambda **kwargs:client)
        def result(value):response.iter_bytes.return_value=[json.dumps({'choices':[{'message':{'content':json.dumps(value)}}]}).encode()]
        yield path,session,client,result


def test_ai_maps_only_exact_profile_references(plan_workspace):
    path,session,client,result=plan_workspace
    result({'mappings':[{'field_id':'0:f1','source':'first_name'}]})
    profile={'first_name':'Jo','application_answers':{'gender':'Man','citizenship':'United States'}}
    plan=browser_plan.propose(path,session,profile)
    assert plan[0]['label']=='Your given name'
    sent=json.dumps(client.stream.call_args.kwargs['json'])
    assert 'citizenship' not in sent and 'United States' not in sent
    assert session.edit.call_count==0  # A proposal does not execute an action.
    session.field_reviews={}
    before=session.snapshot.return_value
    session.snapshot.side_effect=[before,{'fields':[dict(before['fields'][0],value='Jo')]}]
    assert browser_plan.apply(session,profile,plan)==1
    session.edit.assert_called_once_with('0:f1','Jo')
    assert session.field_reviews['0:f1']['pending']


@pytest.mark.parametrize('value',[
    {'actions':[{'click':'Submit'}]},
    {'mappings':[{'field_id':'0:f1','source':'first_name','javascript':'submit()'}]},
    {'mappings':[{'field_id':'missing','source':'first_name'}]},
    {'mappings':[{'field_id':'0:f1','source':'password'}]},
    {'mappings':[{'field_id':'0:f1','source':'first_name'},{'field_id':'0:f1','source':'first_name'}]},
])
def test_ai_rejects_untrusted_actions_before_execution(plan_workspace,value):
    path,session,_,result=plan_workspace;result(value)
    with pytest.raises(ValueError):browser_plan.propose(path,session,{'first_name':'Jo'})
    session.edit.assert_not_called()


def test_ai_will_not_apply_if_question_changed(plan_workspace):
    _,session,_,_=plan_workspace
    plan=[{'field_id':'0:f1','source':'first_name','label':'Old label','type':'text'}]
    assert browser_plan.apply(session,{'first_name':'Jo'},plan)==0
    session.edit.assert_not_called()


def test_sensitive_profile_answers_never_become_ai_sources():
    values=browser_plan.candidates({'first_name':'Jo','application_answers':{
        'gender':'Man','citizenship':'US','disability':'No','school':'Example University','work_authorized':'Yes'}})
    assert values=={'first_name':'Jo','application:school':'Example University'}
