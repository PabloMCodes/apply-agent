"""The local manager opens a tab and has no preview or forwarded input."""
import base64
import json
import os
from pathlib import Path
from urllib.parse import urlparse
import pytest

pytestmark=pytest.mark.skipif(os.environ.get('RUN_BROWSER_TESTS')!='1',reason='Enable Chromium UI tests')


def test_native_preview_focuses_prepared_window_and_opens_review():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser=p.chromium.launch();page=browser.new_page()
        jpeg=base64.b64encode(page.screenshot(type='jpeg')).decode()
        operations=[];errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        record={'id':1,'title':'Engineer','company':'Acme','status':'ready','revision':1,
                'application_url':'https://careers.example.com/apply','snapshot':{'fields':[],'can_submit':False}}
        def respond(route):
            path=urlparse(route.request.url).path
            if path=='/':return route.fulfill(content_type='text/html',body=Path('src/web/index.html').read_text())
            if path.startswith('/static/'):
                return route.fulfill(content_type='text/css' if path.endswith('.css') else 'application/javascript',body=(Path('src/web')/path.rsplit('/',1)[-1]).read_text())
            data={}
            if path=='/browser/status':data={'mode':'native','running':True}
            elif path=='/applications/1':data=record
            elif path.endswith('/learning'):data={'changes_saved':2,'enabled':True,'status':record['status']}
            elif path.endswith('/browser'):
                op=route.request.post_data_json['operation'];operations.append(op)
                if op=='focus':
                    record.update(status='takeover',revision=record['revision']+1)
                    data={'native':True,'token':'test-token','image':jpeg,'tabs':[],'message':'Your prepared browser window is open.'}
                elif op=='review':
                    record.update(status='ready',revision=record['revision']+1)
                    data={'resumed':True}
            route.fulfill(content_type='application/json',body=json.dumps(data))
        page.route('**/*',respond)
        page.goto('http://apply-agent.test/#applications/1')
        preview=page.get_by_role('button',name='Open application tab',exact=True)
        preview.wait_for()
        assert operations==['focus']
        with page.expect_request(lambda r:r.method=='POST' and r.url.endswith('/browser')):
            preview.click()
        page.get_by_text('Your prepared browser window is open.',exact=True).wait_for()
        assert operations==['focus','focus']
        assert page.get_by_label('Browser keyboard input',exact=True).count()==0
        assert page.locator('.native-preview,.live-browser').count()==0
        page.get_by_role('button',name='Review saved application details',exact=True).click()
        page.get_by_role('heading',name='Your answers',exact=True).wait_for()
        assert page.url.endswith('/answers')
        assert operations==['focus','focus','review']
        assert not errors
        browser.close()
