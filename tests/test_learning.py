"""Manual desktop edits become reusable memory; automatic fills and credentials do not."""
import os
import time
from threading import Event
from types import SimpleNamespace
import pytest
from src.applications import store, answers
from src.applications.worker import BrowserWorker
from src.applications.generic import GenericSession
from src.db import database as db
from src.setup import store as settings
from src.telegram import store as telegram
from src.jobs.models import Job

pytestmark=pytest.mark.skipif(os.environ.get('RUN_BROWSER_TESTS')!='1',reason='Enable synthetic Chromium tests')

FORM='''<form id=application><label>First name<input name=first_name></label><label>Last name<input name=last_name></label><label>Email<input name=email type=email></label>
<label>Preferred programming language<input id=answer></label>
<label>Interview format<select id=format><option value="">Select...</option><option value=v>Video</option></select></label>
<fieldset><legend>Willing to relocate?</legend><label><input type=radio name=relocate value=y>Yes</label><label><input type=radio name=relocate value=n>No</label></fieldset>
<label>Electronic signature<input id=signature></label><button type=button>Submit application</button></form>'''


@pytest.fixture
def setup(tmp_path):
    from playwright.sync_api import sync_playwright
    path=tmp_path/'jobs.sqlite3';db.initialize_database(path);settings.initialize(path);telegram.initialize(path);store.initialize(path)
    db.save_profile({'first_name':'Jo','last_name':'Lee','email':'jo@example.com'},path)
    db.save_jobs([Job('Acme','Engineer','Remote','https://careers.example.com/one','manual'),Job('Other','Engineer','Remote','https://careers.example.com/two','manual')],path)
    ids=[store.queue_job(path,i) for i in (1,2)]
    worker=BrowserWorker(path,Event());worker.browser_mode='native'
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=os.environ.get('RUN_NATIVE_BROWSER_TESTS')!='1')
        context=browser.new_context(viewport={'width':1100,'height':850});context.route('**/*',lambda r:r.fulfill(content_type='text/html',body=FORM))
        worker.browser=browser;worker.native_context=context;worker.learner.attach(context)
        yield worker,path,ids
        for app_id in list(worker.sessions):worker.close(app_id)
        browser.close()


def settle(page):page.wait_for_timeout(800)


def test_manual_answers_reuse_and_tabs_share_window(setup):
    worker,path,ids=setup;worker.prepare(ids[0]);first=worker.sessions[ids[0]]['session'].page
    settle(first)
    assert answers.list_answers(path)==[]
    first.locator('#answer').fill('Python');first.locator('#answer').press('Tab')
    # A visible native listbox uses the same select/change events without an OS menu.
    first.locator('#format').evaluate('(el)=>el.size=2')
    first.get_by_role('option',name='Video',exact=True).click()
    assert first.locator('#format').input_value()=='v'
    first.get_by_label('Yes',exact=True).check()
    first.locator('#signature').fill('NOT-REUSABLE');first.locator('#signature').press('Tab')
    settle(first)
    memory=answers.list_answers(path)
    assert {a['value'] for a in memory}=={'Python','Video','Yes'}
    assert all(a['company']=='' for a in memory)
    worker.prepare(ids[1]);second=worker.sessions[ids[1]]['session'].page
    assert second.context is first.context
    assert second.locator('#answer').input_value()=='Python'
    assert second.locator('#format').input_value()=='v'
    assert second.get_by_label('Yes',exact=True).is_checked()
    assert first.context.new_cdp_session(first).send('Browser.getWindowForTarget')['windowId']==second.context.new_cdp_session(second).send('Browser.getWindowForTarget')['windowId']
    settle(second)
    with db.connect(path) as conn:assert conn.execute('select count(*) from learned_answers').fetchone()[0]==3
    worker.close(ids[0]);assert not second.is_closed()


def test_clearing_pausing_company_scope_and_login_exclusion(setup):
    worker,path,ids=setup;worker.prepare(ids[0]);page=worker.sessions[ids[0]]['session'].page
    page.locator('#answer').fill('Python');page.locator('#answer').press('Tab');settle(page)
    page.locator('#answer').fill('');page.locator('#answer').press('Tab');settle(page)
    assert answers.list_answers(path)==[]
    settings.put(path,'preferences',dict(settings.preferences(path),native_learning=False))
    page.locator('#answer').fill('Not saved');page.locator('#answer').press('Tab');settle(page)
    assert answers.list_answers(path)==[]
    settings.put(path,'preferences',dict(settings.preferences(path),native_learning=True))
    page.route('**/extra',lambda r:r.fulfill(content_type='text/html',body='<label>Why join Acme?<textarea></textarea></label><label>Verification code<input name=otp></label>'))
    page.goto('https://careers.example.com/extra')
    page.wait_for_function('()=>window.__applyAgentRecording===true')
    page.locator('textarea').fill('Their product');page.locator('textarea').press('Tab')
    page.locator('input').fill('123456');page.locator('input').press('Tab');settle(page)
    assert answers.list_answers(path)[0]['company']=='acme'
    assert '123456' not in str(answers.list_answers(path))
    page.evaluate("()=>{document.body.innerHTML='<label>Password<input type=password></label><label>Account name<input id=account></label>'}")
    page.locator('#account').fill('LOGIN-NOT-AN-ANSWER');page.locator('#account').press('Tab');settle(page)
    assert len(answers.list_answers(path))==1


def test_custom_dropdown_and_long_answer_are_saved(setup):
    worker,path,ids=setup;worker.prepare(ids[0]);page=worker.sessions[ids[0]]['session'].page
    page.evaluate('''()=>{
      document.body.innerHTML='<div class=select><label for=language>Interview language</label><input id=language role=combobox><span class=select__single-value></span></div><div role=option>Python</div><label>Describe your experience<textarea></textarea></label>';
      document.querySelector('[role=option]').onclick=()=>document.querySelector('.select__single-value').textContent='Python';
    }''')
    page.locator('#language').click();page.get_by_role('option',name='Python').click();settle(page)
    long_answer='Built and shipped products.\n'*150
    page.locator('textarea').fill(long_answer);page.locator('textarea').press('Tab');settle(page)
    memory=answers.list_answers(path)
    assert any(a['field_type']=='combobox' and a['value']=='Python' for a in memory)
    assert any(a['value']==long_answer.strip() for a in memory)
