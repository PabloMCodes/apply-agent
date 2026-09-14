"""Own all Playwright sessions on one thread. Prepare sequentially and retain up to five live reviews for two hours."""

import json
import logging
import os
from pathlib import Path
import shutil
import time

from src.applications import store, answers, questions
from src.applications.greenhouse import GreenhouseSession
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
        self.sessions = {}
        self.browser = None
        self.playwright = None

    def start_browser(self):
        if self.browser is None:
            from playwright.sync_api import sync_playwright
            local_browsers = Path(__file__).resolve().parents[2] / '.playwright'
            if local_browsers.exists():
                os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(local_browsers))
            self.playwright = sync_playwright().start()
            self.browser = self.playwright.chromium.launch(headless=True, channel=os.environ.get('BROWSER_CHANNEL') or None)

    def capture(self, app_id, session):
        directory = self.path.parent / 'applications' / str(app_id)
        directory.mkdir(parents=True, exist_ok=True)
        # Rename so the API never serves half-written screenshots.
        temp = directory / 'review.tmp.png'
        session.page.screenshot(path=str(temp), full_page=True, timeout=15000)
        temp.replace(directory / 'review.png')
        snapshot = session.snapshot()
        snapshot['expires_at'] = self.sessions[app_id]['expires']
        snapshot['entry_url'] = self.sessions[app_id].get('entry_url', session.page.url)
        snapshot['pages'] = [dict(value, page_number=number) for number,value in sorted(session.history.items()) if number != session.page_number]
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
            context = self.browser.new_context(viewport={'width': 1100, 'height': 850}, service_workers='block')
            # Career pages can redirect to external ATS providers, but never private services.
            def guard(route):
                request = route.request
                if not public_request(request.url):
                    route.abort()
                else:
                    route.continue_()
            page = context.new_page()
            page.set_default_timeout(5000)
            context.route('**/*', guard)
            self.sessions[app_id] = {'context': context, 'session': None, 'expires': time.time() + 7200}
            page.goto(url, wait_until='load', timeout=30000)
            adapter, resolved_url = discover(page)
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
            self.fill_pages(app_id, profile, record['company'] or '')
            page.wait_for_timeout(800)
            self.sessions[app_id]['expires'] = time.time() + 7200
            snapshot = self.capture(app_id, session)
            snapshot['entry_url'] = url
            snapshot['adapter'] = adapter
            store.set_run(self.path, app_id, 'ready', snapshot, 'Prepared. Review all answers and the screenshot before submitting.', notify=True)
        except ValueError as exc:
            self.close(app_id)
            store.set_run(self.path, app_id, 'unsupported' if isinstance(exc,UnsupportedForm) else 'needs_attention', message=str(exc), notify=True)
        except Exception as exc:
            self.close(app_id)
            logger.warning('Application %s preparation failed (%s).', app_id, type(exc).__name__)
            store.set_run(self.path, app_id, 'error', message='Could not prepare this form. Check that Chromium is installed and the job is still open. Custom forms may need manual completion.', notify=True)

    def fill_pages(self, app_id, profile, company, advance=True):
        session = self.sessions[app_id]['session']
        for _ in range(20):
            session.autofill(profile, self.sessions[app_id].get('resume_path'))
            questions.flag_conflicts(self.path, profile, session, company)
            session.reused_answers.update(answers.apply(self.path, session, company))
            questions.apply(profile, session)
            snapshot = session.snapshot()
            if not advance or session.submit_button() is not None or not snapshot['can_next'] or self.stop.is_set():
                break
            # Preserve each visited page's values, even when the employer has no review screen.
            session.history[session.page_number] = snapshot
            try:
                session.navigate('next')
            except ValueError:
                break

    def close(self, app_id):
        session = self.sessions.pop(app_id, None)
        if session:
            session['context'].close()

    def handle(self, command):
        app_id = command['application_id']
        kind = command['kind']
        payload = json.loads(command['payload'])
        record = store.get(self.path, app_id)
        if kind == 'retry':
            if app_id not in self.sessions and len(self.sessions) >= settings.preferences(self.path)['review_slots']:
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
                        if time.time() > value['expires']:
                            record = store.get(self.path, app_id)
                            self.close(app_id)
                            store.set_run(self.path, app_id, 'expired', record['snapshot'], 'Review session expired after two hours. Prepare it again to continue.', notify=True)
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
                    elif len(self.sessions) < settings.preferences(self.path)['review_slots']:
                        app_id = store.next_queued(self.path)
                        if app_id:
                            self.prepare(app_id)
                except Exception as exc:
                    logger.warning('Browser worker will retry after %s.', type(exc).__name__)
                self.stop.wait(1)
        finally:
            for app_id in list(self.sessions):
                self.close(app_id)
            if self.browser:
                self.browser.close()
            if self.playwright:
                self.playwright.stop()
