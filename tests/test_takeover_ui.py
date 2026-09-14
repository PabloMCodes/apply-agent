"""End-to-end live browser panel against a synthetic sign-in/application page."""
import os
import socket
import threading
import time
from types import SimpleNamespace
import pytest
import uvicorn
from src.api.app import create_app
from src.applications.worker import BrowserWorker

pytestmark=pytest.mark.skipif(os.environ.get('RUN_BROWSER_TESTS')!='1',reason='Enable synthetic Chromium tests')

LOGIN='''<html><body><label>Password<input id="password" type="password" style="position:absolute;left:100px;top:20px;width:200px;height:30px"></label><button id="login" style="position:absolute;left:320px;top:20px;width:100px;height:30px" onclick="localStorage.setItem('auth','synthetic-token');document.body.innerHTML='<form id=application><label>First name<input name=first_name></label><label>Last name<input name=last_name></label><label>Email<input type=email></label><button type=button>Submit application</button></form>'">Sign in</button></body></html>'''


def test_live_browser_login_and_resume_from_ui(tmp_path):
    from playwright.sync_api import sync_playwright
    path=tmp_path/'ui.sqlite3';stop=threading.Event()
    class Worker(BrowserWorker):
        def start_browser(self):
            if self.browser:return
            self.playwright=sync_playwright().start()
            actual=self.playwright.chromium.launch(channel=os.environ.get('BROWSER_CHANNEL') or None)
            class Context:
                def __init__(self,context):self.context=context
                def __getattr__(self,name):return getattr(self.context,name)
                def route(self,pattern,handler):self.context.route(pattern,lambda r:r.fulfill(status=200,content_type='text/html',body=LOGIN))
            class Browser:
                def new_context(self,**kwargs):return Context(actual.new_context(**kwargs))
                def close(self):actual.close()
            self.browser=Browser()
    app=create_app(path)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error'))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    for _ in range(100):
        if server.started:break
        time.sleep(.05)
    worker=Worker(path,stop);app.state.runtime=SimpleNamespace(browser=worker)
    worker_thread=threading.Thread(target=worker.run,daemon=True);worker_thread.start()
    errors=[]
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(channel=os.environ.get('BROWSER_CHANNEL') or None)
            page=browser.new_page(viewport={'width':1440,'height':1080});page.on('pageerror',lambda e:errors.append(str(e)))
            base=f'http://127.0.0.1:{port}'
            page.request.put(base+'/profile',data={'first_name':'Jo','last_name':'Lee','email':'jo@example.com'})
            job=page.request.post(base+'/jobs',data={'company':'Acme','title':'Engineer','location':'Remote','application_url':'https://careers.example.com/apply'}).json()
            run=page.request.post(base+f"/jobs/{job['id']}/prepare").json()
            page.goto(base+f"/#applications/{run['id']}")
            page.get_by_role('button',name='Open live browser',exact=True).click()
            image=page.get_by_alt_text('Interactive server browser')
            image.wait_for()
            page.wait_for_function("document.querySelector('.live-browser').naturalWidth>0")
            from pathlib import Path
            screenshots=Path('/tmp/apply-agent-takeover-ui');screenshots.mkdir(exist_ok=True)
            page.screenshot(path=str(screenshots/'desktop.png'),full_page=True)
            page.set_viewport_size({'width':390,'height':844})
            page.get_by_label('Zoom browser to full size (scroll to reach controls)',exact=True).check()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=str(screenshots/'phone.png'),full_page=True)
            page.get_by_label('Zoom browser to full size (scroll to reach controls)',exact=True).uncheck()
            page.set_viewport_size({'width':1440,'height':1080})
            # The synthetic password input is near (140, 18) in the server viewport.
            box=image.bounding_box();image.click(position={'x':150*box['width']/1100,'y':35*box['height']/850})
            page.get_by_label('Text to type in browser',exact=True).fill('synthetic-password')
            page.get_by_role('button',name='Type into selected field',exact=True).click()
            page.wait_for_timeout(500)
            assert b'synthetic-password' not in path.read_bytes()
            # The sign-in button is right of the password input.
            image.click(position={'x':370*box['width']/1100,'y':35*box['height']/850})
            page.wait_for_timeout(500)
            page.get_by_role('button',name='Remember this site login',exact=True).click()
            page.get_by_text('Session saved for this application site for up to seven days.',exact=True).wait_for()
            page.get_by_role('button',name='Resume worker / review',exact=True).click()
            page.get_by_role('button',name='Review complete · Submit',exact=True).wait_for()
            response=page.request.get(base+f"/applications/{run['id']}").json()
            assert response['status']=='ready'
            assert next(f for f in response['snapshot']['fields'] if f['name']=='first_name')['value']=='Jo'
            assert not errors
            browser.close()
    finally:
        stop.set();worker_thread.join(timeout=20)
        app.state.runtime=None
        server.should_exit=True;thread.join(timeout=10)
