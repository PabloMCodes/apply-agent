"""Own all Playwright sessions on one thread. Prepare sequentially and retain up to five live reviews for two hours."""

import json
from concurrent.futures import Future
from queue import Queue, Empty, Full
import logging
import re
import os
from pathlib import Path
import shutil
import time

from src.applications import store, answers, questions, native
from src.applications.greenhouse import GreenhouseSession
from src.applications.generic import GenericSession
from src.applications.session_vault import SessionVault
from src.applications import takeover
from src.applications.standard import StandardFormSession
from src.applications.discovery import discover, valid_entry_url, UnsupportedForm
from src.applications.network import public_request
from src.db.database import get_profile, connect
from src.setup import store as settings
from src.setup import resume as resumes

logger = logging.getLogger(__name__)


class BrowserWorker:
    def __init__(self, path, stop):
        self.path, self.stop = path, stop
        self.browser_mode = native.mode()
        self.sessions = {}
        self.browser = None
        self.native_context = None
        self.playwright = None
        self.controls = Queue(maxsize=32)
        self.in_control = False
        self.vault = SessionVault(path)
        from src.applications.live_view import LiveView
        self.live_view = LiveView()
        from src.applications.learning import Learner
        self.learner = Learner(self)

    def start_browser(self):
        if self.browser is None:
            from playwright.sync_api import sync_playwright
            local_browsers = Path(__file__).resolve().parents[2] / '.playwright'
            if local_browsers.exists():
                os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(local_browsers))
            if self.playwright is None:self.playwright = sync_playwright().start()
            self.browser = self.playwright.chromium.launch(headless=self.browser_mode!='native', channel=os.environ.get('BROWSER_CHANNEL') or None)

    def capture(self, app_id, session):
        if takeover.login_page(session.page):
            raise takeover.NeedsTakeover('Login or verification requires browser takeover.')
        if self.browser_mode!='native':
            directory = self.path.parent / 'applications' / str(app_id)
            directory.mkdir(parents=True, exist_ok=True)
            # Rename so the API never serves half-written screenshots.
            temp = directory / 'review.tmp.png'
            session.page.screenshot(path=str(temp), full_page=True, timeout=15000)
            temp.replace(directory / 'review.png')
        snapshot = session.snapshot()
        manual=self.sessions[app_id].get('manual_answers',{})
        changed=False
        for field in snapshot['fields']:
            key=answers.question(field.get('question_label') or field['label'])
            value=field['value']
            if field['type']=='select':value=next((o['label'] for o in field['options'] if o['value']==value),'')
            elif field['type']=='radio':value=field['label'] if value else None
            if key in manual and value is not None and str(value).strip()==manual[key]:
                session.reused_answers.pop(field['id'],None)
                session.field_reviews[field['id']]={'status':'previously_confirmed','source':'Entered by you in the browser; saved to answer memory','pending':False}
                changed=True
        if changed:snapshot=session.snapshot()
        snapshot['native'] = self.browser_mode=='native'
        snapshot['expires_at'] = None if snapshot['native'] else self.sessions[app_id]['expires']
        snapshot['entry_url'] = self.sessions[app_id].get('entry_url', session.page.url)
        snapshot['pages'] = [dict(value, page_number=number) for number,value in sorted(session.history.items()) if number != session.page_number]
        snapshot['ai_message'] = self.sessions[app_id].get('ai_message','')
        snapshot['resume'] = self.sessions[app_id].get('resume')
        snapshot['adapter'] = self.sessions[app_id].get('adapter', 'greenhouse')
        return snapshot

    def prepare(self, app_id):
        record = store.get(self.path, app_id)
        if record.get('job_status') in (None, 'skipped', 'applied'):
            store.set_run(self.path, app_id, 'cancelled', message='The job was removed, skipped, or marked applied. Preparation was stopped.')
            return
        url = record['application_url']
        if not valid_entry_url(url):
            store.set_run(self.path, app_id, 'unsupported', message='Use a public HTTPS application URL without embedded credentials.', notify=True)
            return
        profile = get_profile(self.path) or {}
        if not all(profile.get(key) for key in ('first_name', 'last_name', 'email')):
            store.set_run(self.path, app_id, 'needs_attention', message='Complete your first name, last name, and email in Profile, then retry.', notify=True)
            return
        store.set_run(self.path, app_id, 'preparing', message='Opening the application and filling confirmed profile details.')
        try:
            self.start_browser()
            saved_state = self.vault.load(url)
            context_options = {'viewport':{'width':1100,'height':850},'service_workers':'block'}
            if saved_state: context_options['storage_state'] = saved_state
            shared=self.browser_mode=='native'
            context=self.native_context if shared else None
            fresh=context is None
            if fresh:
                context=self.browser.new_context(**context_options)
                if shared:
                    self.native_context=context
                    self.learner.attach(context)
            elif saved_state:
                # Keep active login cookies; add missing saved cookies for another site.
                existing={(c['name'],c['domain'],c['path']) for c in context.cookies()}
                context.add_cookies([c for c in saved_state.get('cookies',[]) if (c['name'],c['domain'],c['path']) not in existing])
                origins=[{'origin':o['origin'],'localStorage':o.get('localStorage',[])} for o in saved_state.get('origins',[])]
                context.add_init_script('for (const o of '+json.dumps(origins)+') { if(location.origin===o.origin) for(const v of o.localStorage) if(localStorage.getItem(v.name)===null) localStorage.setItem(v.name,v.value); }')
            # Career pages can redirect to external ATS providers, but never private services.
            def guard(route):
                request = route.request
                if not public_request(request.url):
                    route.abort()
                else:
                    route.continue_()
            page = context.new_page()
            page.set_default_timeout(5000)
            if fresh:context.route('**/*', guard)
            self.sessions[app_id] = {'context': context, 'session': GenericSession(page), 'expires': time.time() + 7200, 'entry_url':url,'adapter':'generic','automating':True}
            if shared:
                self.sessions[app_id]['pages']=[page]
                page.on('popup',lambda popup:self.sessions.get(app_id,{}).get('pages',[]).append(popup))
            else:self.live_view.attach(app_id,page)
            self.freeze_resume(app_id,record)
            page.goto(url, wait_until='load', timeout=30000)
            if takeover.login_page(page):
                self.pause_for_takeover(app_id,'Sign in using the live browser, then resume preparation.')
                return
            try:
                adapter, resolved_url = discover(page)
            except UnsupportedForm:
                self.pause_for_takeover(app_id, 'This page needs your help. Open the live browser to sign in or operate the form, then resume preparation.')
                return
            if adapter == 'greenhouse' and page.url != resolved_url:
                page.goto(resolved_url, wait_until='load', timeout=30000)
            session = GreenhouseSession(page) if adapter == 'greenhouse' else StandardFormSession(page)
            self.sessions[app_id]['session'] = session
            self.sessions[app_id]['entry_url'] = url
            self.sessions[app_id]['adapter'] = adapter
            if adapter == 'greenhouse':
                page.locator('input[type="email"], input[name="email"], input[id="email"]').first.wait_for(timeout=20000)
            if adapter == 'greenhouse' and page.locator('.select-shell').count():
                page.wait_for_function("""() => {
                    const email = document.querySelector('input[id="email"], input[name="email"]');
                    return email && Object.keys(email).some(key => key.startsWith('__reactProps'));
                }""", timeout=10000)
            self.fill_pages(app_id, profile, record['company'] or '')
            page.wait_for_timeout(800)
            self.sessions[app_id]['expires'] = time.time() + 7200
            snapshot = self.capture(app_id, session)
            snapshot['entry_url'] = url
            snapshot['adapter'] = adapter
            store.set_run(self.path, app_id, 'ready', snapshot, 'Prepared. Review all answers and the screenshot before submitting.', notify=True)
        except takeover.NeedsTakeover as exc:
            self.pause_for_takeover(app_id,str(exc))
        except ValueError as exc:
            self.close(app_id)
            store.set_run(self.path, app_id, 'unsupported' if isinstance(exc,UnsupportedForm) else 'needs_attention', message=str(exc), notify=True)
        except Exception as exc:
            if app_id in self.sessions and self.sessions[app_id].get('session'):
                self.pause_for_takeover(app_id, 'Automatic preparation paused. Open the live browser to inspect the page and continue.')
                return
            self.close(app_id)
            logger.warning('Application %s preparation failed (%s).', app_id, type(exc).__name__)
            store.set_run(self.path, app_id, 'error', message='Could not prepare this form. Check that Chromium is installed and the job is still open. Custom forms may need manual completion.', notify=True)

        finally:
            if self.browser_mode=='native' and app_id in self.sessions:
                self.learner.set_recording(self.sessions[app_id],True)

    def freeze_resume(self, app_id, record):
        resume, reason = resumes.choose(self.path, app_id, record['title'] or '')
        self.sessions[app_id]['resume'] = {'id':resume['id'],'title':resume['title'],'reason':reason} if resume else None
        resume_path = None
        if resume:
            directory = self.path.parent / 'applications' / str(app_id)
            directory.mkdir(parents=True, exist_ok=True)
            for suffix in ('.pdf','.txt'):
                (directory / ('resume'+suffix)).unlink(missing_ok=True)
            resume_path = directory / ('resume' + Path(resume['filename']).suffix)
            shutil.copyfile(self.path.parent / 'resumes' / resume['filename'], resume_path)
        self.sessions[app_id]['resume_path'] = resume_path

    def fill_pages(self, app_id, profile, company, advance=True):
        entry=self.sessions[app_id]
        previous=entry.get('automating',True)
        if self.browser_mode=='native':self.learner.set_recording(entry,False)
        try:self._fill_pages(app_id,profile,company,advance)
        finally:
            if self.browser_mode=='native':self.learner.set_recording(entry,not previous)

    def _fill_pages(self, app_id, profile, company, advance=True):
        session = self.sessions[app_id]['session']
        for _ in range(20):
            if not self.in_control:self.service_control()
            if takeover.login_page(session.page):
                raise takeover.NeedsTakeover('Login or verification requires browser takeover.')
            session.autofill(profile, self.sessions[app_id].get('resume_path'))
            questions.flag_conflicts(self.path, profile, session, company)
            session.reused_answers.update(answers.apply(self.path, session, company))
            questions.apply(profile, session)
            ai_config=settings.get(self.path,'ai',{})
            if ai_config.get('enabled') and ai_config.get('browser_assistance'):
                page_key=(session.page.url,session.page_number)
                seen=self.sessions[app_id].setdefault('ai_pages',set())
                if page_key not in seen:
                    seen.add(page_key)
                    try:
                        from src.ai.browser_plan import propose,apply
                        apply(session,profile,propose(self.path,session,profile))
                    except ValueError:
                        self.sessions[app_id]['ai_message']='AI field mapping was unavailable. Unanswered fields need your review.'
            snapshot = session.snapshot()
            if not advance or session.submit_button() is not None or not snapshot['can_next'] or self.stop.is_set():
                break
            # Preserve each visited page's values, even when the employer has no review screen.
            session.history[session.page_number] = snapshot
            try:
                session.navigate('next')
            except ValueError:
                break

    def pause_for_takeover(self, app_id, message):
        entry = self.sessions[app_id]
        previous = store.get(self.path,app_id)['snapshot']
        snapshot = dict(previous, adapter=entry['adapter'],can_submit=False,takeover=True,
                        url=takeover.clean_url(entry['session'].page.url),expires_at=entry['expires'])
        # Never persist screenshots or form values from a login page.
        store.set_run(self.path,app_id,'takeover',snapshot,message,notify=True)

    def request_control(self,app_id,payload):
        future=Future()
        try:self.controls.put_nowait((app_id,payload,future))
        except Full:raise ValueError('Browser command queue is full. Try again shortly.') from None
        return future

    def service_control(self):
        try:app_id,payload,future=self.controls.get_nowait()
        except Empty:return False
        try:
            if future.set_running_or_notify_cancel():
                try:
                    self.in_control=True
                    future.set_result(self.control(app_id,payload))
                except ValueError as exc:future.set_exception(ValueError(str(exc)))
                except Exception:future.set_exception(ValueError('The browser action could not complete. Refresh the view before continuing.'))
        finally:
            self.in_control=False
            payload.clear()  # Do not retain typed passwords or verification codes.
            self.controls.task_done()
        return True

    def control(self,app_id,payload):
        entry=self.sessions.get(app_id)
        record=store.get(self.path,app_id)
        if not entry or not record or record['status'] not in ('ready','takeover'):
            raise ValueError('No live browser session. Prepare the application again.')
        if record.get('job_status') in (None,'skipped','applied'):
            raise ValueError('This job was removed, skipped, or marked applied. Close this browser session.')
        if self.browser_mode!='native' and time.time()>entry['expires']:
            raise ValueError('This review session expired. Prepare the application again.')
        operation=payload['operation'];session=entry['session'];page=session.page
        if page.is_closed():
            pages=[p for p in entry.get('pages',entry['context'].pages) if not p.is_closed()]
            if not pages:raise ValueError('All browser tabs were closed. Prepare the application again.')
            page=pages[-1];session=entry.get('tab_sessions',{}).get(page) or GenericSession(page)
            entry['session']=session;entry['adapter']='generic' if isinstance(session,GenericSession) else entry['adapter']
        if self.browser_mode!='native':self.live_view.attach(app_id,page)
        if operation=='focus':
            if self.browser_mode!='native':raise ValueError('This session runs in streamed mode. Restart with BROWSER_MODE=native and prepare it again for a desktop window.')
            self.pause_for_takeover(app_id,'Open in your desktop browser. Finish and review the application there.')
            focused=native.focus(page)
            return dict(native=True,message='Your prepared browser window is open.' if focused else 'Select the Chromium window in your Dock to continue.')
        if operation=='native_submitted':
            if self.browser_mode!='native':raise ValueError('Manual browser completion is available in native mode only.')
            store.set_run(self.path,app_id,'submitted',record['snapshot'],'Marked submitted by you in the desktop browser.')
            self.close(app_id)
            return {'submitted':True,'message':'Application marked submitted.'}
        if operation=='start':
            if record['status']=='ready':self.pause_for_takeover(app_id,'You control the browser. Resume preparation when finished.')
            return takeover.image_frame(entry)
        if operation=='review' and record['status']=='ready':
            return {'resumed':True,'message':'Ready for answer review.'}
        if record['status']!='takeover':raise ValueError('Open browser takeover before operating this page.')
        if self.browser_mode!='native' and operation!='refresh' and payload.get('token')!=entry.get('control_token'):
            raise ValueError('The browser view changed. Refresh before another action.')
        if operation in ('click','type','insert','key','mark_final','upload_resume') and entry.get('view_signature') != takeover.view_signature(page):
            raise ValueError('The page changed since the screenshot. Refresh before interacting.')
        message=''
        if operation in ('click','mark_final'):
            message=takeover.click_target(session,payload['x'],payload['y'],operation=='mark_final')
        elif operation=='upload_resume':
            if takeover.login_page(page):raise ValueError('Finish login before uploading a resume.')
            target=takeover.at_point(page,payload['x'],payload['y'])
            if not entry.get('resume_path'):raise ValueError('Choose a resume and prepare this application again.')
            if target.evaluate("e=>e.tagName==='INPUT' && e.type==='file'"):
                target.set_input_files(str(entry['resume_path']))
            elif re.fullmatch(r'attach|upload|upload resume|choose file|choose resume|browse|resume/cv',target.inner_text().strip(),re.I):
                with page.expect_file_chooser(timeout=5000) as chooser:
                    target.click(timeout=5000)
                chooser.value.set_files(str(entry['resume_path']))
            else:
                raise ValueError('Click the resume file input, its label, or a supported upload button.')
            message='Uploaded the selected resume. Review the employer form.'
        elif operation=='type':takeover.type_text(page,payload['text'])
        elif operation=='insert':takeover.insert_text(page,payload['text'])
        elif operation=='scroll':
            page.mouse.move(payload.get('x',550),payload.get('y',425))
            page.mouse.wheel(0,payload['delta'])
        elif operation=='key':page.keyboard.press(payload['key'])
        elif operation=='tab':
            tabs=entry.get('pages',entry['context'].pages)
            if payload['tab']>=len(tabs):raise ValueError('Tab is no longer available.')
            saved_tabs=entry.setdefault('tab_sessions',{})
            saved_tabs[session.page]=session
            selected=tabs[payload['tab']];selected.bring_to_front()
            entry['session']=saved_tabs.get(selected) or GenericSession(selected)
            entry['adapter']='generic' if isinstance(entry['session'],GenericSession) else 'standard' if isinstance(entry['session'],StandardFormSession) else 'greenhouse'
        elif operation=='save_session':
            if takeover.login_page(page):raise ValueError('Finish signing in before saving this session.')
            self.vault.save(entry['entry_url'],entry['context'].storage_state(indexed_db=True))
            message='Session saved for this application site for up to seven days.'
        elif operation=='review':
            if takeover.login_page(page):raise ValueError('Finish signing in before reviewing application answers.')
            store.set_run(self.path,app_id,'ready',self.capture(app_id,session),'Review your answers before submitting.')
            return {'resumed':True,'message':'Ready for answer review.'}
        elif operation in ('resume','ai'):
            if takeover.login_page(page):raise ValueError('Finish signing in or verification before resuming.')
            if operation=='resume' and not getattr(session,'final_target',None):
                try:
                    adapter,url=discover(page,timeout=.5)
                    if url!=page.url:page.goto(url,wait_until='domcontentloaded',timeout=20000)
                    replacement=GreenhouseSession(page) if adapter=='greenhouse' else StandardFormSession(page)
                    replacement.history=session.history;replacement.page_number=session.page_number
                    replacement.field_reviews=session.field_reviews;replacement.reused_answers=session.reused_answers
                    session=replacement;entry['session']=session;entry['adapter']=adapter
                except UnsupportedForm:
                    if not isinstance(session,GenericSession):
                        replacement=GenericSession(page);replacement.history=session.history;replacement.page_number=session.page_number
                        session=replacement;entry['session']=session;entry['adapter']='generic'
            profile=get_profile(self.path) or {}
            self.fill_pages(app_id,profile,record['company'] or '',advance=False)
            if operation=='ai':
                from src.ai.browser_plan import propose,apply
                plan=propose(self.path,session,profile)
                filled=apply(session,profile,plan)
                message=f'AI mapped {filled} fields to saved facts. Check every marked answer.'
            else:message='Preparation resumed. Review the current page and all earlier answers.'
            self.fill_pages(app_id,profile,record['company'] or '',advance=True)
            store.set_run(self.path,app_id,'ready',self.capture(app_id,session),message)
            return {'resumed':True,'message':message}
        elif operation!='refresh':raise ValueError('Unsupported browser operation.')
        if self.browser_mode=='native':return {'native':True,'message':message}
        self.live_view.attach(app_id,entry['session'].page)
        return dict(takeover.image_frame(entry),message=message)

    def close(self, app_id):
        self.live_view.close(app_id)
        session = self.sessions.pop(app_id, None)
        if session:
            if self.browser_mode=='native' and session['context'] is self.native_context:
                for page in session.get('pages',[session['session'].page]):
                    if not page.is_closed():page.close()
            else:session['context'].close()

    def handle(self, command):
        app_id = command['application_id']
        kind = command['kind']
        payload = json.loads(command['payload'])
        record = store.get(self.path, app_id)
        if kind == 'retry':
            if self.browser_mode!='native' and app_id not in self.sessions and len(self.sessions) >= settings.preferences(self.path)['review_slots']:
                store.set_run(self.path, app_id, record['status'], record['snapshot'], 'All review slots are occupied. Close or submit one before retrying.')
                return
            self.close(app_id)
            self.prepare(app_id)
            return
        if kind == 'cancel':
            self.close(app_id)
            store.set_run(self.path, app_id, 'cancelled', record['snapshot'], 'Browser session closed. Nothing was submitted.')
            return
        if app_id not in self.sessions:
            store.set_run(self.path, app_id, 'expired', record['snapshot'], 'The browser session expired. Prepare a fresh application before reviewing.')
            return
        session = self.sessions[app_id]['session']
        if record['status'] != 'ready' or record['revision'] != payload['revision']:
            return
        if kind in ('navigate','suggest'):
            try:
                if kind == 'navigate':
                    session.navigate(payload['direction'])
                    self.fill_pages(app_id, get_profile(self.path) or {}, record['company'] or '', advance=False)
                else:
                    from src.ai.provider import suggest
                    field = next(f for f in session.snapshot()['fields'] if f['id']==payload['field_id'])
                    if field.get('review',{}).get('conflict'):
                        raise ValueError('Resolve conflicting facts yourself before requesting a draft.')
                    session.field_reviews[field['id']] = suggest(self.path, get_profile(self.path) or {}, field)
                store.set_run(self.path, app_id, 'ready', self.capture(app_id,session), 'Review the current page and any suggested answers.')
            except ValueError as exc:
                store.set_run(self.path, app_id, 'ready', self.capture(app_id,session), str(exc))
            return
        if kind in ('edit', 'options'):
            try:
                if kind == 'options':
                    session.find_options(payload['field_id'], payload['query'])
                    questions.apply(get_profile(self.path) or {}, session)
                else:
                    session.edit(payload['field_id'], payload['value'])
                    session.reused_answers.pop(payload['field_id'], None)
                    session.field_reviews[payload['field_id']] = {'status':'previously_confirmed','source':'Confirmed by you on this application','pending':False}
                    if payload.get('remember'):
                        field = next(f for f in session.snapshot()['fields'] if f['id'] == payload['field_id'])
                        if field['value'] != payload['value']:
                            raise ValueError('The employer did not retain this answer.')
                        answers.remember(self.path, field, record['company'] or '', payload.get('scope', 'company'))
                snapshot = self.capture(app_id, session)
                message = 'Choices loaded. Select an employer-provided option and save it.' if kind == 'options' else 'Answer updated. Review the latest application.'
                store.set_run(self.path, app_id, 'ready', snapshot, message)
            except Exception:
                snapshot = self.capture(app_id, session)
                store.set_run(self.path, app_id, 'ready', snapshot, 'Could not change that field. Check its available options or complete it on the employer site.')
        elif kind == 'submit':
            if record.get('job_status') in (None, 'skipped', 'applied'):
                self.close(app_id)
                store.set_run(self.path, app_id, 'cancelled', record['snapshot'], 'The job was removed, skipped, or marked applied. Submission was stopped.')
                return
            live = session.snapshot()
            if live['fingerprint'] != record['snapshot'].get('fingerprint') or not live['can_submit']:
                store.set_run(self.path, app_id, 'ready', self.capture(app_id, session), 'The form changed or needs more answers. Review again before submitting.')
                return
            # Write this state BEFORE the external click. A restart must not retry it.
            store.set_run(self.path, app_id, 'submitting', record['snapshot'], 'Submitting the application you reviewed.')
            try:
                outcome = session.submit(live['fingerprint'])
            except ValueError:
                store.set_run(self.path, app_id, 'ready', self.capture(app_id, session), 'The form changed. Please review it again.')
                return
            except Exception:
                outcome = 'submission_unknown'
            message = ('The employer confirmed your application was submitted.' if outcome == 'submitted' else
                       'Submission outcome could not be confirmed. Check the employer or your email before taking any further action. We will not retry automatically.')
            try:
                snapshot = self.capture(app_id, session)
            except Exception:
                snapshot = record['snapshot']
            store.set_run(self.path, app_id, outcome, snapshot, message, notify=True)
            self.close(app_id)

    def run(self):
        store.recover(self.path)
        try:
            while not self.stop.is_set():
                try:
                    for app_id, value in list(self.sessions.items()):
                        if store.get(self.path, app_id).get('job_status') == 'applied':
                            self.close(app_id)
                            continue
                        if not any(not p.is_closed() for p in value.get('pages',value['context'].pages)):
                            previous=store.get(self.path,app_id)
                            self.close(app_id)
                            store.set_run(self.path,app_id,'needs_attention',snapshot=previous['snapshot'],message='Browser window closed. Check whether you submitted before preparing again.')
                            continue
                        if self.browser_mode!='native' and time.time() > value['expires']:
                            record = store.get(self.path, app_id)
                            self.close(app_id)
                            store.set_run(self.path, app_id, 'expired', record['snapshot'], 'Review session expired after two hours. Prepare it again to continue.', notify=True)
                    if self.browser is not None and hasattr(self.browser,'is_connected') and not self.browser.is_connected():
                        self.browser=None;self.native_context=None
                    if self.service_control():
                        continue
                    command = store.next_command(self.path)
                    if command:
                        try:
                            self.handle(command)
                        except Exception as exc:
                            logger.warning('Browser action failed (%s).', type(exc).__name__)
                            record = store.get(self.path, command['application_id'])
                            status = 'submission_unknown' if record['status'] == 'submitting' else 'error'
                            store.set_run(self.path, command['application_id'], status, record['snapshot'], 'Browser action failed. Review the application state before continuing.')
                            self.close(command['application_id'])
                        finally:
                            store.complete_command(self.path, command['id'])
                    elif self.browser_mode=='native' or len(self.sessions) < settings.preferences(self.path)['review_slots']:
                        app_id = store.next_queued(self.path)
                        if app_id:
                            self.prepare(app_id)
                except Exception as exc:
                    logger.warning('Browser worker will retry after %s.', type(exc).__name__)
                try:
                    pages=[e['session'].page for e in self.sessions.values() if not e['session'].page.is_closed()]
                    if pages:pages[0].wait_for_timeout(30)  # Pump live frames and input promptly.
                    else:self.stop.wait(.1)
                except Exception:
                    self.stop.wait(.1)  # A desktop window can close between the check and poll.
        finally:
            while not self.controls.empty():
                _,payload,future=self.controls.get_nowait()
                payload.clear()
                future.cancel()
            for app_id in list(self.sessions):
                self.close(app_id)
            if self.browser:
                self.browser.close()
            if self.playwright:
                self.playwright.stop()
