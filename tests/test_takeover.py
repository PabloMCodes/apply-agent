"""Synthetic browser takeover tests. No real identity provider or employer calls."""
import json
import os
from threading import Event
import pytest
from src.applications.worker import BrowserWorker
from src.applications.generic import GenericSession
from src.applications import store, takeover
from src.applications.session_vault import SessionVault
from src.db import database as db
from src.setup import store as settings
from src.telegram import store as telegram
from src.jobs.models import Job


@pytest.fixture
def workspace(tmp_path):
    path=tmp_path/'jobs.sqlite3'
    db.initialize_database(path);settings.initialize(path);telegram.initialize(path);store.initialize(path)
    db.save_profile({'first_name':'Jo','last_name':'Lee','email':'jo@example.com'},path)
    db.save_jobs([Job('Acme','Engineer','Remote','https://careers.example.com/apply','manual')],path)
    app_id=store.queue_job(path,1)
    return path,app_id


def test_vault_encrypts_scopes_expires_and_forgets(workspace,monkeypatch):
    path,_=workspace;vault=SessionVault(path)
    state={'cookies':[{'name':'auth','value':'SECRETCOOKIE','domain':'careers.example.com'}],'origins':[]}
    vault.save('https://careers.example.com/apply',state)
    assert b'SECRETCOOKIE' not in vault.target('https://careers.example.com').read_bytes()
    assert vault.load('https://careers.example.com/another')==state
    assert vault.load('https://different.example.com') is None
    assert vault.target('https://careers.example.com').stat().st_mode & 0o777==0o600
    assert vault.list()[0]['origin']=='https://careers.example.com'
    from src.applications import session_vault
    now=session_vault.time.time();monkeypatch.setattr(session_vault.time,'time',lambda:now+8*86400)
    assert vault.load('https://careers.example.com') is None
    vault.forget('https://careers.example.com')
    assert vault.list()==[]


def test_remote_credentials_do_not_enter_database(workspace):
    path,app_id=workspace;worker=BrowserWorker(path,Event())
    secret={'operation':'type','text':'PASSWORD-MUST-STAY-IN-MEMORY'}
    future=worker.request_control(app_id,secret)
    worker.control=lambda app,payload: {'ok':True}
    assert worker.service_control()
    assert future.result()=={'ok':True}
    assert secret=={}
    assert b'PASSWORD-MUST-STAY-IN-MEMORY' not in path.read_bytes()
    pending={'operation':'type','text':'cancelled secret'}
    future=worker.request_control(app_id,pending);future.cancel()
    worker.control=lambda *args:pytest.fail('Cancelled action replayed')
    worker.service_control();assert pending=={}


@pytest.fixture
def browser_page():
    if os.environ.get('RUN_BROWSER_TESTS')!='1':pytest.skip('Enable synthetic Chromium tests')
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser=p.chromium.launch(channel=os.environ.get('BROWSER_CHANNEL') or None)
        context=browser.new_context(viewport={'width':1100,'height':850})
        context.route('**/*',lambda r:r.fulfill(status=200,content_type='text/html',body='<body></body>'))
        page=context.new_page();page.goto('https://careers.example.com/apply')
        yield page
        browser.close()


def worker_for(path,app_id,page):
    worker=BrowserWorker(path,Event());session=GenericSession(page)
    worker.sessions[app_id]={'context':page.context,'session':session,'entry_url':page.url,'adapter':'generic','expires':__import__('time').time()+7200}
    store.set_run(path,app_id,'ready',{'fields':[]})
    return worker,session


def center(page,selector):
    box=page.locator(selector).bounding_box()
    return {'x':box['x']+box['width']/2,'y':box['y']+box['height']/2}


def test_login_takeover_same_context_save_and_resume(browser_page,workspace):
    page=browser_page;path,app_id=workspace
    page.set_content('''<label>Email<input id=email></label><label>Password<input id=password type=password></label>
<button id=login onclick="document.body.innerHTML='<form id=application><label>First name<input name=first_name></label><label>Last name<input name=last_name></label><label>Email<input type=email></label><button type=button>Submit application</button></form>'">Sign in</button>''')
    worker,session=worker_for(path,app_id,page)
    view=worker.control(app_id,{'operation':'start'})
    view=worker.control(app_id,{'operation':'click','token':view['token'],**center(page,'#password')})
    view=worker.control(app_id,{'operation':'type','token':view['token'],'text':'transient-password'})
    assert page.locator('#password').input_value()=='transient-password'
    assert b'transient-password' not in path.read_bytes()
    assert not (path.parent/'applications'/str(app_id)/'review.png').exists()
    with pytest.raises(ValueError,match='signing in'):
        worker.control(app_id,{'operation':'save_session','token':view['token']})
    view=worker.control(app_id,{'operation':'click','token':view['token'],**center(page,'#login')})
    page.context.add_cookies([{'name':'auth','value':'cookie-value','url':'https://careers.example.com'}])
    view=worker.control(app_id,{'operation':'save_session','token':view['token']})
    assert worker.vault.load(page.url)['cookies'][0]['value']=='cookie-value'
    result=worker.control(app_id,{'operation':'resume','token':view['token']})
    assert result['resumed']
    assert worker.sessions[app_id]['context'] is page.context
    assert page.locator('[name=first_name]').input_value()=='Jo'
    assert store.get(path,app_id)['status']=='ready'


def test_stale_click_and_final_submission_are_blocked(browser_page,workspace):
    page=browser_page;path,app_id=workspace
    page.set_content('<form><label>Full name<input value="Jo Lee"></label><button type=button id=finish onclick="window.submitted=true">Submit application</button></form>')
    worker,session=worker_for(path,app_id,page)
    view=worker.control(app_id,{'operation':'start'})
    with pytest.raises(ValueError,match='Final submission'):
        worker.control(app_id,{'operation':'click','token':view['token'],**center(page,'#finish')})
    page.locator('#finish').evaluate("e=>e.style.marginTop='100px'")
    with pytest.raises(ValueError,match='page changed'):
        worker.control(app_id,{'operation':'click','token':view['token'],**center(page,'#finish')})
    view=worker.control(app_id,{'operation':'refresh'})
    view=worker.control(app_id,{'operation':'mark_final','token':view['token'],**center(page,'#finish')})
    assert not page.evaluate('Boolean(window.submitted)')
    result=worker.control(app_id,{'operation':'resume','token':view['token']})
    assert result['resumed'] and store.get(path,app_id)['snapshot']['can_submit']
    assert not page.evaluate('Boolean(window.submitted)')


def test_popup_tab_is_available_in_same_context(browser_page,workspace):
    page=browser_page;path,app_id=workspace;worker,_=worker_for(path,app_id,page)
    view=worker.control(app_id,{'operation':'start'})
    popup=page.context.new_page();popup.goto('https://careers.example.com/login')
    view=worker.control(app_id,{'operation':'refresh'})
    assert len(view['tabs'])==2
    view=worker.control(app_id,{'operation':'tab','token':view['token'],'tab':1})
    assert worker.sessions[app_id]['session'].page is popup


def test_unknown_page_is_kept_for_takeover(browser_page,workspace,monkeypatch):
    page=browser_page;path,app_id=workspace;worker=BrowserWorker(path,Event())
    # Use the synthetic context instead of real external traffic.
    class Context:
        def __getattr__(self,name):return getattr(page.context,name)
        def route(self,pattern,handler):page.context.route(pattern,lambda r:r.fulfill(status=200,content_type='text/html',body='<h1>Custom application</h1>'))
    class Browser:
        def new_context(self,**kwargs):return Context()
    worker.browser=Browser()
    monkeypatch.setattr('src.applications.worker.public_request',lambda _:True)
    from src.applications.discovery import UnsupportedForm
    monkeypatch.setattr('src.applications.worker.discover',lambda *args,**kwargs:(_ for _ in ()).throw(UnsupportedForm('custom page')))
    worker.prepare(app_id)
    assert store.get(path,app_id)['status']=='takeover'
    assert app_id in worker.sessions


def test_saved_state_restores_in_new_context_and_mfa_is_redacted(browser_page,workspace):
    page=browser_page;path,_=workspace;vault=SessionVault(path)
    page.context.add_cookies([{'name':'auth','value':'restore-me','url':'https://careers.example.com'}])
    page.evaluate("localStorage.setItem('signed-in','yes')")
    vault.save(page.url,page.context.storage_state(indexed_db=True))
    fresh=page.context.browser.new_context(storage_state=vault.load(page.url))
    try:
        assert fresh.cookies('https://careers.example.com')[0]['value']=='restore-me'
        assert fresh.cookies('https://another.example.com')==[]
    finally:fresh.close()
    page.set_content('<label>Verification code<input name=otp value=123456></label>')
    snapshot=GenericSession(page).snapshot()
    assert snapshot['fields'][0]['value']==''
    assert not snapshot['fields'][0]['supported']
    assert '123456' not in json.dumps(snapshot)


def test_marked_final_button_change_requires_new_review(browser_page):
    page=browser_page
    page.set_content('<label>Name<input value=Jo></label><button id=final type=button>Send application</button>')
    session=GenericSession(page)
    takeover.click_target(session,**center(page,'#final'),mark_final=True)
    assert session.snapshot()['can_submit']
    page.locator('#final').evaluate("e=>e.textContent='Delete account'")
    assert not session.snapshot()['can_submit']


def test_empty_username_credentials_are_rejected():
    from src.applications.discovery import valid_entry_url
    from src.applications.session_vault import origin
    assert not valid_entry_url('https://:secret@example.com/apply')
    with pytest.raises(ValueError):origin('https://:secret@example.com/apply')


def test_mfa_capture_is_never_written_to_disk(browser_page,workspace):
    page=browser_page;path,app_id=workspace
    page.set_content('<label>Verification code<input name=code value=123456></label>')
    worker,session=worker_for(path,app_id,page)
    with pytest.raises(takeover.NeedsTakeover):worker.capture(app_id,session)
    assert not (path.parent/'applications'/str(app_id)/'review.png').exists()


def test_apply_link_uses_navigation_and_dropdown_button_is_allowed(browser_page):
    page=browser_page
    page.set_content('''<button aria-haspopup="listbox" onclick="this.dataset.open='yes'">Select an option</button>
<a href="https://careers.example.com/form" onclick="window.unsafeClick=true;return false">Apply now</a>''')
    session=GenericSession(page)
    takeover.click_target(session,**center(page,'button'))
    assert page.locator('button').get_attribute('data-open')=='yes'
    takeover.click_target(session,**center(page,'a'))
    assert page.url=='https://careers.example.com/form'
    assert page.evaluate('window.unsafeClick') is None


def test_explicit_selected_resume_upload_through_file_chooser(browser_page,workspace,tmp_path):
    page=browser_page;path,app_id=workspace
    page.set_content('''<input id=resume type=file hidden>
<button type=button onclick="document.querySelector('#resume').click()">Upload resume</button>''')
    worker,session=worker_for(path,app_id,page)
    resume=tmp_path/'selected.pdf';resume.write_bytes(b'%PDF-1.4 synthetic resume')
    worker.sessions[app_id]['resume_path']=resume
    view=worker.control(app_id,{'operation':'start'})
    worker.control(app_id,{'operation':'upload_resume','token':view['token'],**center(page,'button')})
    assert page.locator('#resume').evaluate('e=>e.files[0].name')=='selected.pdf'


def test_profile_demographic_native_dropdowns_fill_but_block_submission(browser_page):
    from src.applications.questions import apply
    page=browser_page
    page.set_content('''<form id=application>
<label>Gender*<select><option value="">Select...</option><option value=m>Male</option></select></label>
<label>Veteran Status*<select><option value="">Select...</option><option value=n>I am not a protected veteran</option></select></label>
<button type=button>Submit application</button></form>''')
    session=GenericSession(page)
    apply({'application_answers':{'gender':'Man','veteran':'I am not a protected veteran'}},session)
    assert page.locator('select').nth(0).input_value()=='m'
    assert page.locator('select').nth(1).input_value()=='n'
    snapshot=session.snapshot()
    assert all(f['review']['pending'] for f in snapshot['fields'] if f['type']=='select')
    assert not snapshot['can_submit']


def test_profile_choice_fills_greenhouse_custom_dropdown(browser_page):
    from src.applications.questions import apply
    page=browser_page
    page.set_content('''<div class=select><label for=gender>Gender*</label>
<span class=select__single-value></span><input id=gender role=combobox></div>''')
    page.evaluate('''() => {
      const div=document.querySelector('.select'), input=document.querySelector('input');
      function choices(){
        div.querySelector('[role=listbox]')?.remove();
        const list=document.createElement('div');list.setAttribute('role','listbox');
        for(const name of ['Male','Female','Decline to self-identify']){
          const option=document.createElement('div');option.setAttribute('role','option');option.textContent=name;
          option.onclick=()=>{div.querySelector('.select__single-value').textContent=name;input.value='';list.remove();};
          list.append(option);
        }
        div.append(list);
      }
      input.onclick=choices;input.oninput=choices;
      input.onkeydown=e=>{if(e.key==='Escape')div.querySelector('[role=listbox]')?.remove();};
    }''')
    session=GenericSession(page)
    apply({'application_answers':{'gender':'Man'}},session)
    field=session.snapshot()['fields'][0]
    assert field['value']=='Male'
    assert field['review']['pending']
    assert field['review']['status']=='new_wording'


def test_live_frames_update_without_control_and_are_discarded(browser_page):
    from src.applications.live_view import LiveView
    page=browser_page;view=LiveView();view.attach(1,page)
    try:
        page.set_content('<input aria-label="First name"><div style="height:2000px">Application</div>')
        for _ in range(30):
            page.wait_for_timeout(50)
            if view.read(1):break
        first=view.read(1)
        assert first and first['image']
        page.locator('input').fill('Jo')
        for _ in range(30):
            page.wait_for_timeout(50)
            if view.read(1)['image']!=first['image']:break
        assert view.read(1)['image']!=first['image']
    finally:view.close(1)
    assert view.read(1) is None


def test_direct_input_preserves_caret_and_scrolls(browser_page,workspace):
    from src.setup.schemas import BrowserControl
    page=browser_page;path,app_id=workspace
    page.set_content('<input aria-label="Name"><div style="height:2400px">Application</div>')
    worker,_=worker_for(path,app_id,page)
    view=worker.control(app_id,{'operation':'start'})
    view=worker.control(app_id,{'operation':'click','token':view['token'],**center(page,'input')})
    for text in ['J','o']:
        view=worker.control(app_id,{'operation':'insert','token':view['token'],'text':text})
    assert page.locator('input').input_value()=='Jo'
    view=worker.control(app_id,{'operation':'key','token':view['token'],'key':'ArrowLeft'})
    view=worker.control(app_id,{'operation':'insert','token':view['token'],'text':'X'})
    assert page.locator('input').input_value()=='JXo'
    worker.control(app_id,{'operation':'scroll','token':view['token'],'delta':500})
    page.wait_for_function('window.scrollY>0')
    with pytest.raises(ValueError):BrowserControl(operation='key',key='Enter')


def test_live_frame_endpoint_is_read_only_and_not_cacheable(workspace):
    from fastapi.testclient import TestClient
    from types import SimpleNamespace
    from src.api.app import create_app
    from src.applications.live_view import LiveView
    path,app_id=workspace
    with TestClient(create_app(path)) as client:
        view=LiveView();view.frames[app_id]={'image':'synthetic','width':1100,'height':850}
        client.app.state.runtime=SimpleNamespace(browser=SimpleNamespace(live_view=view))
        store.set_run(path,app_id,'preparing',{})
        before=store.get(path,app_id)
        result=client.get(f'/applications/{app_id}/browser/frame')
        assert result.status_code==200 and result.json()['status']=='preparing'
        assert result.headers['cache-control']=='no-store'
        assert store.get(path,app_id)['revision']==before['revision']
        store.set_run(path,app_id,'cancelled',{})
        assert client.get(f'/applications/{app_id}/browser/frame').status_code==409
        client.app.state.runtime=None


def test_return_to_review_captures_manual_edits_without_filling_or_advancing(browser_page,workspace):
    page=browser_page;path,app_id=workspace
    page.set_content('<label>First name<input id=first></label><label>Last name<input id=last></label><button type=button>Submit application</button>')
    worker,_=worker_for(path,app_id,page)
    view=worker.control(app_id,{'operation':'start'})
    page.locator('#first').fill('Manual edit')
    result=worker.control(app_id,{'operation':'review','token':view['token']})
    assert result['resumed']
    record=store.get(path,app_id)
    assert record['status']=='ready'
    assert page.locator('#last').input_value()==''
    assert any(f['value']=='Manual edit' for f in record['snapshot']['fields'])


def test_native_focus_reuses_page_and_manual_completion_does_not_click_submit(browser_page,workspace,monkeypatch):
    from src.applications import native
    page=browser_page;path,app_id=workspace
    page.set_content('<label>First name<input value=Jo></label><button type=button onclick="window.submitted=true">Submit application</button>')
    worker,_=worker_for(path,app_id,page);worker.browser_mode='native'
    focused=[];monkeypatch.setattr(native,'focus',lambda p:focused.append(p) or True)
    result=worker.control(app_id,{'operation':'focus'})
    assert result['native'] and focused==[page]
    assert len(page.context.pages)==1 and page.locator('input').input_value()=='Jo'
    assert not page.evaluate('Boolean(window.submitted)')
    assert store.get(path,app_id)['status']=='takeover'
    result=worker.control(app_id,{'operation':'native_submitted'})
    assert result['submitted'] and app_id not in worker.sessions
    assert store.get(path,app_id)['job_status']=='applied'
    assert 'Marked submitted by you' in store.get(path,app_id)['message']


def test_stream_mode_rejects_native_focus(browser_page,workspace):
    path,app_id=workspace;worker,_=worker_for(path,app_id,browser_page)
    worker.browser_mode='stream'
    with pytest.raises(ValueError,match='streamed mode'):
        worker.control(app_id,{'operation':'focus'})


@pytest.mark.skipif(os.environ.get('RUN_NATIVE_BROWSER_TESTS')!='1',reason='Explicit desktop browser test')
def test_real_desktop_browser_focus_preserves_prepared_form(workspace,monkeypatch):
    path,app_id=workspace;monkeypatch.setenv('BROWSER_MODE','native')
    worker=BrowserWorker(path,Event());worker.start_browser()
    try:
        context=worker.browser.new_context(viewport={'width':1100,'height':850})
        context.route('**/*',lambda r:r.fulfill(status=200,content_type='text/html',body='<label>First name<input name=first_name></label><button type=button>Submit application</button>'))
        page=context.new_page();page.goto('https://careers.example.com/apply')
        session=GenericSession(page);session.autofill({'first_name':'Jo'})
        page.evaluate("localStorage.setItem('session-marker','same-browser')")
        worker.sessions[app_id]={'context':context,'session':session,'entry_url':page.url,'adapter':'generic','expires':__import__('time').time()+7200}
        store.set_run(path,app_id,'ready',{})
        result=worker.control(app_id,{'operation':'focus'})
        assert result['native'] and result['message']=='Your prepared browser window is open.'
        assert worker.sessions[app_id]['session'].page is page
        assert page.locator('input').input_value()=='Jo'
        assert page.evaluate("localStorage.getItem('session-marker')")=='same-browser'
        assert len(context.pages)==1
    finally:
        worker.close(app_id);worker.browser.close();worker.playwright.stop()


def test_closed_desktop_window_keeps_review_history_and_does_not_mark_applied(workspace,monkeypatch):
    from types import SimpleNamespace
    path,app_id=workspace;worker=BrowserWorker(path,Event())
    snapshot={'fields':[{'label':'First name','value':'Jo'}],'pages':[]}
    store.set_run(path,app_id,'ready',snapshot)
    worker.sessions[app_id]={'context':SimpleNamespace(pages=[],close=lambda:None),'expires':__import__('time').time()+7200}
    monkeypatch.setattr(store,'recover',lambda path:None)
    def stop_after_cleanup():worker.stop.set();return True
    worker.service_control=stop_after_cleanup
    worker.run()
    record=store.get(path,app_id)
    assert record['status']=='needs_attention'
    assert record['snapshot']==snapshot
    assert record['job_status']!='applied'
    assert worker.sessions=={}
