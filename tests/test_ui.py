"""Opt-in desktop/mobile browser smoke test against an isolated local web server."""

import os
from pathlib import Path
import socket
import threading
import time

import pytest
import uvicorn
from src.api.app import create_app

pytestmark = pytest.mark.skipif(os.environ.get('RUN_BROWSER_TESTS') != '1', reason='Set RUN_BROWSER_TESTS=1 with Chromium installed.')


def test_setup_interface_desktop_and_phone(tmp_path):
    from playwright.sync_api import sync_playwright
    with socket.socket() as socket_:
        socket_.bind(('127.0.0.1',0))
        port=socket_.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(create_app(tmp_path/'ui.sqlite3'),host='127.0.0.1',port=port,log_level='error'))
    thread=threading.Thread(target=server.run,daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:break
        time.sleep(.05)
    assert server.started
    errors=[]
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(channel=os.environ.get('BROWSER_CHANNEL') or None)
            page=browser.new_page(viewport={'width':1440,'height':1080})
            page.on('pageerror',lambda error:errors.append(str(error)))
            base=f'http://127.0.0.1:{port}'
            page.goto(base)
            page.get_by_role('heading',name='Your next chapter, with a little help.').wait_for()
            page.get_by_role('link',name='Profile & resume',exact=False).click()
            page.get_by_label('First name',exact=True).fill('Jordan')
            page.get_by_label('Last name',exact=True).fill('Taylor')
            page.get_by_label('Email address',exact=True).fill('jordan@example.com')
            assert page.get_by_text('null',exact=True).count()==0
            assert page.get_by_label('Experience and projects',exact=True).count()==0
            assert page.get_by_role('button',name='Add a fact',exact=True).count()==0
            page.get_by_label('Protected veteran status',exact=True).select_option('Prefer not to disclose')
            page.get_by_label('Country these eligibility answers apply to',exact=True).fill('United States')
            page.get_by_label('Are you legally authorized to work in this country?',exact=True).select_option('Yes')
            page.get_by_role('button',name='Save profile',exact=True).click()
            page.get_by_role('status').filter(has_text='Profile saved.').wait_for()
            page.get_by_label('New resume title',exact=True).fill('Design')
            page.locator('#resume-upload').set_input_files({'name':'resume.txt','mimeType':'text/plain','buffer':b'Alex Example\nalex@example.com | 212-555-0199\ngithub.com/alexdev\nEducation\nExample University\nBachelor of Science in Computer Science\nExpected graduation May 2027\nSkills\nPython, React, SQL'})
            page.get_by_role('button',name='Upload resume',exact=True).click()
            page.get_by_text('Uploaded: resume.txt',exact=True).wait_for()
            assert page.get_by_label('First name',exact=True).input_value()=='Jordan'
            assert page.get_by_label('Email address',exact=True).input_value()=='jordan@example.com'
            assert page.get_by_label('GitHub URL',exact=True).input_value()=='https://github.com/alexdev'
            assert page.get_by_label('School / university',exact=True).input_value()=='Example University'
            assert page.get_by_label('Gender',exact=True).input_value()==''
            assert page.get_by_label('Protected veteran status',exact=True).input_value()=='Prefer not to disclose'
            page.get_by_role('button',name='Save profile',exact=True).click()
            screenshots=Path(os.environ.get('SCREENSHOT_DIR',str(tmp_path)))
            screenshots.mkdir(parents=True,exist_ok=True)
            page.screenshot(path=str(screenshots/'profile-desktop.png'),full_page=True)
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            page.screenshot(path=str(screenshots/'profile-mobile.png'),full_page=True)
            page.set_viewport_size({'width':1440,'height':1080})
            page.get_by_role('link',name='Preferences',exact=False).first.click()
            page.get_by_label('Roles or title keywords',exact=True).fill('designer, marketing')
            page.get_by_role('button',name='Save preferences').click()
            page.get_by_role('status').filter(has_text='Preferences saved.').wait_for()
            page.get_by_role('link',name='Job sources',exact=False).click()
            page.get_by_label('Company or source name',exact=True).fill('Acme')
            page.get_by_label('Job board or application URL',exact=True).fill('https://job-boards.greenhouse.io/acme')
            page.get_by_role('button',name='Add source',exact=True).click()
            page.get_by_role('heading',name='Acme',exact=True).wait_for()
            page.get_by_role('link',name='Telegram',exact=False).click()
            page.get_by_role('heading',name='Telegram connection',exact=True).wait_for()
            page.get_by_role('link',name='Applications',exact=False).click()
            page.get_by_role('heading',name='You make the first move.',exact=True).wait_for()
            for index in range(5):
                response=page.request.post(base+'/jobs',data={'company':'Acme','title':f'Designer {index}','location':'Remote','application_url':f'https://example.com/apply/{index}'})
                assert response.ok
            page.goto(base+'/#jobs')
            page.get_by_label('Select Designer 0 at Acme',exact=True).wait_for()
            for index in range(5):
                page.get_by_label(f'Select Designer {index} at Acme',exact=True).check()
            page.get_by_role('button',name='Prepare selected (5)',exact=True).click()
            page.get_by_role('heading',name='Your applications',exact=True).wait_for()
            assert page.locator('.application-item').count()==5
            page.get_by_text('Add and select applications',exact=True).click()
            page.get_by_role('button',name='Select all matching jobs',exact=True).click()
            page.get_by_role('button',name='Prepare selected (5)',exact=True).wait_for()
            page.get_by_role('heading',name='Saved answers',exact=True).wait_for()

            page.get_by_role('link',name='Overview',exact=False).click()
            page.get_by_role('heading',name='A workspace that knows you',exact=True).wait_for()
            screenshots=Path(os.environ.get('SCREENSHOT_DIR',str(tmp_path)))
            screenshots.mkdir(parents=True,exist_ok=True)
            page.screenshot(path=str(screenshots/'overview-desktop.png'),full_page=True)
            page.set_viewport_size({'width':390,'height':844})
            page.screenshot(path=str(screenshots/'overview-mobile.png'),full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            page.goto(base+'/#profile')
            page.get_by_label('First name',exact=True).wait_for()
            assert page.get_by_label('First name',exact=True).input_value()=='Jordan'
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            assert not errors
            browser.close()
    finally:
        server.should_exit=True
        thread.join(timeout=10)
