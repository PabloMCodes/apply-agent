"""Opt-in local Chromium tests. All employer pages are synthetic; no real submissions."""

import os
from threading import Event
import pytest

from src.applications.greenhouse import GreenhouseSession
from src.applications.worker import BrowserWorker
from src.applications import store as apps
from src.db import database as db
from src.jobs.models import Job
from src.setup import store as setup
from src.telegram import store as telegram

pytestmark = pytest.mark.skipif(os.environ.get('RUN_BROWSER_TESTS') != '1', reason='Set RUN_BROWSER_TESTS=1 with Chromium installed.')

FORM = '''<!doctype html><html><body><h1>Designer at Acme</h1><form id="application">
<label>First Name *<input required name="first_name"></label>
<label>Last Name *<input required name="last_name"></label>
<label>Email *<input required type="email" name="email"></label>
<label>Phone<input name="phone"></label>
<label>Resume<input type="file" name="resume" accept=".pdf,.txt"></label>
<label>Why this role? *<textarea required name="question"></textarea></label>
<label>Work arrangement<select name="arrangement"><option value="">Choose</option><option value="remote">Remote</option></select></label>
<label>I confirm these answers<input type="checkbox" required name="consent"></label>
<button type="submit">Submit application</button></form>
<script>window.submitted=0;document.querySelector('form').addEventListener('submit',event=>{event.preventDefault();window.submitted++;document.body.innerHTML='<h1>Thank you for applying</h1>';});</script>
</body></html>'''
PROFILE = {'first_name':'Jo','last_name':'Lee','email':'jo@example.com','phone':'1234567890'}


@pytest.fixture
def page():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=os.environ.get('BROWSER_CHANNEL') or None)
        page = browser.new_page()
        page.route('**/*', lambda route: route.fulfill(status=200,content_type='text/html',body=FORM))
        page.goto('https://job-boards.greenhouse.io/acme/jobs/1')
        yield page
        browser.close()


def test_autofill_pauses_and_user_edits_then_explicit_submit(page, tmp_path):
    resume = tmp_path/'resume.txt';resume.write_text('Design experience')
    session = GreenhouseSession(page)
    session.autofill(PROFILE, resume)
    snapshot = session.snapshot()
    assert page.evaluate('window.submitted') == 0
    assert page.locator('[name=first_name]').input_value() == 'Jo'
    assert snapshot['can_submit'] is False
    for field in snapshot['fields']:
        if field['name']=='question':session.edit(field['id'],'I enjoy designing useful products.')
        if field['name']=='consent':session.edit(field['id'],True)
        if field['name']=='arrangement':session.edit(field['id'],'remote')
    approved = session.snapshot()
    assert approved['can_submit'] is True
    assert session.submit(approved['fingerprint']) == 'submitted'
    assert page.evaluate('window.submitted') == 1


def test_changed_form_invalidates_approval(page):
    session = GreenhouseSession(page)
    session.autofill(PROFILE)
    page.locator('[name=question]').fill('My answer')
    page.locator('[name=consent]').check()
    old = session.snapshot()
    page.locator('[name=email]').fill('changed@example.com')
    with pytest.raises(ValueError,match='changed'):
        session.submit(old['fingerprint'])
    assert page.evaluate('window.submitted') == 0


def test_unknown_required_custom_controls_block_submit(page):
    page.evaluate("document.querySelector('form').insertAdjacentHTML('afterbegin','<label>Country *<input required role=combobox name=country></label>')")
    session = GreenhouseSession(page)
    session.autofill(PROFILE)
    assert any('manual completion' in text for text in session.snapshot()['blockers'])
    assert page.evaluate('window.submitted') == 0


def test_browser_worker_persists_review_without_submitting(page,tmp_path):
    path=tmp_path/'jobs.sqlite3'
    db.initialize_database(path);telegram.initialize(path);setup.initialize(path);apps.initialize(path)
    db.save_profile(PROFILE,path)
    db.save_jobs([Job('Acme','Designer','Remote',page.url,'manual')],path)
    app_id=apps.queue_job(path,1)
    session=GreenhouseSession(page);session.autofill(PROFILE)
    worker=BrowserWorker(path,Event())
    import time
    worker.sessions[app_id]={'session':session,'expires':time.time()+900,'context':page.context}
    snapshot=worker.capture(app_id,session)
    apps.set_run(path,app_id,'ready',snapshot,'Prepared for review.')
    assert apps.get(path,app_id)['status']=='ready'
    assert (path.parent/'applications'/str(app_id)/'review.png').exists()
    assert page.evaluate('window.submitted')==0


def test_known_greenhouse_dropdown_and_hidden_validation(page):
    page.evaluate('''() => {
      const div=document.createElement('div'); div.className='select';
      div.innerHTML='<label for="country">Country*</label><span class="select__single-value"></span><input id="country" role="combobox" aria-required="true"><input required aria-hidden="true" tabindex="-1">';
      document.querySelector('form').prepend(div);
      const input=div.querySelector('#country');
      function choices(){
        div.querySelector('[role=listbox]')?.remove();
        const list=document.createElement('div'); list.setAttribute('role','listbox');
        for(const name of ['United States','Canada']){
          const option=document.createElement('div');option.setAttribute('role','option');option.textContent=name;
          option.onclick=()=>{div.querySelector('.select__single-value').textContent=name;input.value='';list.remove();};
          list.append(option);
        }
        div.append(list);
      }
      input.onclick=choices;input.oninput=choices;
      input.onkeydown=e=>{if(e.key==='Escape')div.querySelector('[role=listbox]')?.remove();};
    }''')
    session=GreenhouseSession(page)
    snapshot=session.snapshot()
    country=next(f for f in snapshot['fields'] if f['name']=='country')
    assert country['type']=='combobox' and country['supported']
    assert not any(f['label']=='Unlabelled field' for f in snapshot['fields'])
    choices=session.find_options(country['id'])
    assert choices[0]['value']=='United States'
    session.edit(country['id'],'Canada')
    assert next(f for f in session.snapshot()['fields'] if f['name']=='country')['value']=='Canada'
    assert page.evaluate('window.submitted')==0


def test_full_worker_preparation_and_review_command(page,tmp_path):
    path=tmp_path/'jobs.sqlite3'
    db.initialize_database(path);telegram.initialize(path);setup.initialize(path);apps.initialize(path)
    db.save_profile(PROFILE,path)
    db.save_jobs([Job('Acme','Designer','Remote',page.url,'manual')],path)
    app_id=apps.queue_job(path,1)
    worker=BrowserWorker(path,Event())
    real_browser=page.context.browser
    class MockContext:
        def __init__(self,context):self.context=context
        def new_page(self):return self.context.new_page()
        def route(self,pattern,handler):
            self.context.route(pattern,lambda route:route.fulfill(status=200,content_type='text/html',body=FORM))
        def close(self):self.context.close()
    class MockBrowser:
        def new_context(self,**kwargs):return MockContext(real_browser.new_context(**kwargs))
    worker.browser=MockBrowser()
    try:
        worker.prepare(app_id)
        record=apps.get(path,app_id)
        assert record['status']=='ready'
        assert next(f for f in record['snapshot']['fields'] if f['name']=='first_name')['value']=='Jo'
        question=next(f for f in record['snapshot']['fields'] if f['name']=='question')
        apps.command(path,app_id,'edit',{'revision':record['revision'],'field_id':question['id'],'value':'A confirmed answer'})
        command=apps.next_command(path)
        worker.handle(command);apps.complete_command(path,command['id'])
        current=apps.get(path,app_id)
        assert current['revision']>record['revision']
        assert next(f for f in current['snapshot']['fields'] if f['name']=='question')['value']=='A confirmed answer'
        assert worker.sessions[app_id]['session'].page.evaluate('window.submitted')==0
        # Five distinct contexts can remain filled while the first awaits review.
        for index in range(2,6):
            db.save_jobs([Job('Acme',f'Designer {index}','Remote',f'https://job-boards.greenhouse.io/acme/jobs/{index}','manual')],path)
            next_id=apps.queue_job(path,index)
            worker.prepare(next_id)
            assert apps.get(path,next_id)['status']=='ready'
        assert len(worker.sessions)==5
        assert all(v['session'].page.evaluate('window.submitted')==0 for v in worker.sessions.values())
        assert next(f for f in worker.sessions[app_id]['session'].snapshot()['fields'] if f['name']=='question')['value']=='A confirmed answer'
    finally:
        for run_id in list(worker.sessions):
            worker.close(run_id)


def test_careerpuck_style_wrapper_resolves_embedded_greenhouse(page):
    from src.applications.discovery import discover
    page.route('https://app.careerpuck.com/**',lambda route:route.fulfill(status=200,content_type='text/html',body='<h1>Lyft job</h1><iframe src="https://job-boards.greenhouse.io/embed/job_app?for=lyft&token=8797837002"></iframe>'))
    page.goto('https://app.careerpuck.com/job-board/lyft/job/8797837002')
    adapter,url=discover(page,timeout=3)
    assert adapter=='greenhouse'
    assert url=='https://job-boards.greenhouse.io/embed/job_app?for=lyft&token=8797837002'


def test_common_application_form_excludes_unrelated_fields(page):
    from src.applications.discovery import discover
    from src.applications.standard import StandardFormSession
    page.goto('https://careers.example.com/apply/1')
    page.evaluate("document.body.insertAdjacentHTML('afterbegin','<form><label>Newsletter email<input type=email name=newsletter></label></form>')")
    assert discover(page,timeout=2)[0]=='standard'
    session=StandardFormSession(page)
    session.autofill(PROFILE)
    assert page.locator('[name=first_name]').input_value()=='Jo'
    assert page.locator('[name=newsletter]').input_value()==''
    assert page.evaluate('window.submitted')==0


def test_login_form_is_not_treated_as_application(page):
    from src.applications.discovery import discover,UnsupportedForm
    page.goto('https://careers.example.com/login')
    page.set_content('<form id="application"><input name="first_name"><input type=email><input type=password><button>Submit application</button></form>')
    with pytest.raises(UnsupportedForm):discover(page,timeout=.1)


def test_remembered_answer_requires_confirmation(page, tmp_path):
    from src.applications import answers
    import json
    import time
    path = tmp_path/'jobs.sqlite3'
    db.initialize_database(path);telegram.initialize(path);setup.initialize(path);apps.initialize(path)
    db.save_jobs([Job('Acme','Designer','Remote',page.url,'manual')],path)
    app_id=apps.queue_job(path,1)
    session=GreenhouseSession(page)
    session.autofill(PROFILE)
    field=next(f for f in session.snapshot()['fields'] if f['name']=='question')
    answers.remember(path, dict(field,value='I enjoy design.'), 'Acme', 'company')
    session.reused_answers=answers.apply(path,session,'Acme')
    page.locator('[name=consent]').check()
    snapshot=session.snapshot()
    assert not snapshot['can_submit']
    assert any('Confirm remembered answer' in b for b in snapshot['blockers'])
    with pytest.raises(ValueError):
        session.submit(snapshot['fingerprint'])
    worker=BrowserWorker(path,Event())
    worker.sessions[app_id]={'session':session,'context':page.context,'expires':time.time()+7200}
    apps.set_run(path,app_id,'ready',snapshot)
    worker.handle({'application_id':app_id,'kind':'edit','payload':json.dumps({
        'revision':1,'field_id':field['id'],'value':'I enjoy design.','remember':True,'scope':'company'})})
    assert apps.get(path,app_id)['snapshot']['can_submit']
    assert page.evaluate('window.submitted') == 0
    assert len(answers.list_answers(path)) == 1


MULTIPAGE = '''<html><body><form id="application"></form><script>
let step=0;window.submitted=0;const values={};
const pages=[`<h1>Application details</h1><label>First name<input name="first_name" required></label><label>Last name<input name="last_name" required></label><label>Email<input name="email" type="email" required></label><button type="button" onclick="move(1)">Next</button>`,
`<h1>Experience</h1><label>Describe your projects<textarea name="projects" required></textarea></label><button type="button" onclick="move(-1)">Back</button><button type="button" onclick="move(1)">Review application</button>`,
`<h1>Review application</h1><p>Check your application</p><button type="button" onclick="move(-1)">Back</button><button type="submit">Submit application</button>`];
function draw(){document.querySelector('form').innerHTML=pages[step];for(const e of document.querySelectorAll('input,textarea'))e.value=values[e.name]||'';}
function move(n){for(const e of document.querySelectorAll('input,textarea'))values[e.name]=e.value;step+=n;draw();}
document.querySelector('form').onsubmit=e=>{e.preventDefault();window.submitted++;document.body.innerHTML='Thank you for applying';};draw();
</script></body></html>'''


def test_multistep_history_pending_mapping_and_back_review(page,tmp_path):
    from src.applications.discovery import discover
    from src.applications.standard import StandardFormSession
    from src.applications import questions
    page.goto('https://careers.example.com/apply')
    page.set_content(MULTIPAGE)
    assert discover(page,timeout=1)[0]=='standard'
    session=StandardFormSession(page)
    session.autofill(PROFILE)
    assert session.snapshot()['can_next']
    assert not session.snapshot()['can_submit']
    session.navigate('next')
    assert session.page_number==2
    profile={'facts':[{'id':'p','question':'Tell us about projects','answer':'Built a tool','variants':['Describe your projects']} ]}
    questions.apply(profile,session)
    current=session.snapshot()
    assert current['fields'][0]['review']['status']=='new_wording'
    session.navigate('next')
    assert session.page_number==3
    assert not session.snapshot()['can_submit']
    assert any('page 2' in b for b in session.snapshot()['blockers'])
    assert session.history[1]['fields'][0]['value']=='Jo'
    session.navigate('back')
    field=session.snapshot()['fields'][0]
    assert field['value']=='Built a tool'
    session.edit(field['id'],'Built a tool')
    session.field_reviews[field['id']]={'status':'previously_confirmed','pending':False,'source':'Confirmed by you'}
    session.navigate('next')
    assert session.snapshot()['can_submit']
    assert page.evaluate('window.submitted')==0
    assert session.submit(session.snapshot()['fingerprint'])=='submitted'


def test_multistep_missing_answer_and_ambiguous_navigation_pause(page):
    from src.applications.standard import StandardFormSession
    from src.applications.discovery import discover
    page.goto('https://careers.example.com/apply');page.set_content(MULTIPAGE)
    discover(page,timeout=1)
    session=StandardFormSession(page);session.autofill(PROFILE);session.navigate('next')
    with pytest.raises(ValueError,match='required'):
        session.navigate('next')
    page.locator('[name=projects]').fill('A project')
    page.evaluate("document.querySelector('form').insertAdjacentHTML('beforeend','<button type=button>Next</button>')")
    assert not session.snapshot()['can_next']
    assert page.evaluate('window.submitted')==0


def test_worker_advances_to_review_and_records_all_pages(page,tmp_path):
    path=tmp_path/'jobs.sqlite3'
    db.initialize_database(path);telegram.initialize(path);setup.initialize(path);apps.initialize(path)
    db.save_profile(dict(PROFILE,facts=[{'id':'p','question':'Describe your projects','answer':'Built a tool'}]),path)
    db.save_jobs([Job('Acme','Designer','Remote','https://careers.example.com/apply','manual')],path)
    app_id=apps.queue_job(path,1)
    worker=BrowserWorker(path,Event())
    real_browser=page.context.browser
    class Context:
        def __init__(self,c): self.context=c
        def new_page(self): return self.context.new_page()
        def route(self,pattern,handler): self.context.route(pattern,lambda r:r.fulfill(status=200,content_type='text/html',body=MULTIPAGE))
        def close(self): self.context.close()
    class Browser:
        def new_context(self,**kwargs): return Context(real_browser.new_context(**kwargs))
    worker.browser=Browser()
    try:
        worker.prepare(app_id)
        record=apps.get(path,app_id)
        assert record['status']=='ready'
        snapshot=record['snapshot']
        assert snapshot['page_number']==3
        assert len(snapshot['pages'])==2
        assert snapshot['pages'][1]['fields'][0]['value']=='Built a tool'
        assert not snapshot['can_submit']
        assert worker.sessions[app_id]['session'].page.evaluate('window.submitted')==0
    finally:
        worker.close(app_id)


def test_final_page_without_employer_review_preserves_history(page):
    from src.applications.standard import StandardFormSession
    from src.applications.discovery import discover
    page.goto('https://careers.example.com/apply');page.set_content(MULTIPAGE)
    discover(page,timeout=1)
    session=StandardFormSession(page);session.autofill(PROFILE);session.navigate('next')
    # This employer submits from the last question page without a separate review screen.
    page.get_by_role('button',name='Review application',exact=True).evaluate("e=>{e.textContent='Submit application';e.removeAttribute('onclick');e.type='submit'}")
    page.locator('[name=projects]').fill('Built a tool')
    assert session.snapshot()['can_submit']
    assert session.history[1]['fields'][0]['value']=='Jo'
    assert session.snapshot()['can_back']
    assert page.evaluate('window.submitted')==0
