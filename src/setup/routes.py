"""Local setup API used by the bundled web interface."""

import secrets
import sqlite3
import time
from urllib.parse import urlparse
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Response
from fastapi.responses import FileResponse

from src.setup import store, resume, resume_profile
from src.setup.schemas import Preferences, SourceInput, SourceUpdate, TelegramConnect, EditField, ReviewAction, FieldOptions, PrepareBatch, BulkJobs, ResumeUpdate, AISettings, Navigate, SuggestAnswer
from src.db import database as db
from src.jobs.sources import classify, load_source
from src.jobs.fetch import FetchError
from src.jobs.parser import ParseError
from src.telegram import store as telegram_store
from src.telegram.client import TelegramClient, TelegramError
from src.applications import store as applications
from src.applications import answers
from src.applications.discovery import valid_entry_url


def router(path):
    routes = APIRouter()

    @routes.get('/settings')
    def settings():
        return store.preferences(path)

    @routes.put('/settings')
    def update_settings(body: Preferences):
        return store.put(path, 'preferences', body.model_dump())

    @routes.get('/resume')
    def resume_info():
        return store.get(path, 'resume', None)

    @routes.post('/resume')
    async def upload_resume(file: UploadFile = File()):
        content = await file.read(resume.MAX_SIZE + 1)
        await file.close()
        try:
            text, suffix = resume.extract(file.filename or '', content)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        filename = resume.save_file(path, content, suffix)
        metadata = {'filename': filename, 'original_name': (file.filename or 'resume')[:200],
                    'text': text, 'size': len(content),
                    'warning': '' if text.strip() else 'No text found. Paste resume text and confirm your profile; scanned PDFs need OCR.'}
        old = store.get(path, 'resume')
        store.put(path, 'resume', metadata)
        if old:
            with db.connect(path) as conn:
                conn.execute('DELETE FROM resumes WHERE filename=?',(old['filename'],))
            (path.parent / 'resumes' / old['filename']).unlink(missing_ok=True)
        return dict(metadata, profile_suggestions=resume_profile.suggest(text))

    @routes.get('/resume/file')
    def download_resume():
        metadata = store.get(path, 'resume')
        if not metadata:
            raise HTTPException(404, 'No resume uploaded.')
        return FileResponse(path.parent / 'resumes' / metadata['filename'], filename=metadata['original_name'])

    @routes.delete('/resume', status_code=204)
    def delete_resume():
        old = store.get(path, 'resume')
        store.put(path, 'resume', None)
        if old:
            with db.connect(path) as conn:
                conn.execute('DELETE FROM resumes WHERE filename=?',(old['filename'],))
            (path.parent / 'resumes' / old['filename']).unlink(missing_ok=True)
        return Response(status_code=204)

    @routes.get('/profile/fields')
    def profile_fields():
        from src.setup.application_fields import FIELDS
        return FIELDS

    @routes.get('/resumes/{resume_id}/profile-suggestions')
    def resume_suggestions(resume_id: int):
        item = next((r for r in resume.list_resumes(path) if r['id']==resume_id),None)
        if not item:
            raise HTTPException(404,'Resume not found.')
        return resume_profile.suggest(item['text'])

    @routes.get('/resumes')
    def resume_library():
        return resume.list_resumes(path)

    @routes.post('/resumes', status_code=201)
    async def add_resume(title: str = Form(min_length=1, max_length=200), roles: str = Form(default='', max_length=2000), file: UploadFile = File()):
        if not title.strip():
            raise HTTPException(422, 'Give this resume a title.')
        content = await file.read(resume.MAX_SIZE+1)
        await file.close()
        try:
            text, suffix = resume.extract(file.filename or '', content)
        except ValueError as exc:
            raise HTTPException(422,str(exc)) from exc
        filename = resume.save_file(path, content, suffix)
        with db.connect(path) as conn:
            row = conn.execute('INSERT INTO resumes(title,roles,filename,original_name,text,size) VALUES (?,?,?,?,?,?)',
                (title.strip(),roles.strip(),filename,(file.filename or 'resume')[:200],text,len(content)))
            result = dict(conn.execute('SELECT * FROM resumes WHERE id=?',(row.lastrowid,)).fetchone())
        return dict(result, profile_suggestions=resume_profile.suggest(text))

    @routes.patch('/resumes/{resume_id}')
    def rename_resume(resume_id: int, body: ResumeUpdate):
        with db.connect(path) as conn:
            if not conn.execute('UPDATE resumes SET title=?,roles=? WHERE id=?',(body.title,body.roles,resume_id)).rowcount:
                raise HTTPException(404,'Resume not found.')
        return {'updated':True}

    @routes.get('/resumes/{resume_id}/file')
    def resume_file(resume_id: int):
        item = next((r for r in resume.list_resumes(path) if r['id']==resume_id),None)
        if not item:
            raise HTTPException(404,'Resume not found.')
        return FileResponse(path.parent/'resumes'/item['filename'],filename=item['original_name'])

    @routes.delete('/resumes/{resume_id}', status_code=204)
    def remove_resume(resume_id: int):
        with db.connect(path) as conn:
            item = conn.execute('SELECT * FROM resumes WHERE id=?',(resume_id,)).fetchone()
            if not item:
                raise HTTPException(404,'Resume not found.')
            conn.execute('DELETE FROM resumes WHERE id=?',(resume_id,))
        old = store.get(path,'resume')
        if old and old['filename']==item['filename']:
            store.put(path,'resume',None)
        (path.parent/'resumes'/item['filename']).unlink(missing_ok=True)
        return Response(status_code=204)

    @routes.get('/settings/ai')
    def ai_settings():
        value = store.get(path,'ai',{})
        return {k:v for k,v in value.items() if k!='api_key'} | {'has_key':bool(value.get('api_key'))}

    @routes.put('/settings/ai')
    def save_ai(body: AISettings):
        value = body.model_dump()
        if body.api_key is None:
            old = store.get(path,'ai',{})
            value['api_key'] = old.get('api_key','') if old.get('base_url') == body.base_url else ''
        store.put(path,'ai',value)
        return ai_settings()

    @routes.post('/jobs/bulk', status_code=201)
    def bulk_jobs(body: BulkJobs):
        from src.jobs.models import Job
        jobs = [Job(j.company,j.title,j.location,str(j.application_url),j.source) for j in body.jobs]
        db.save_jobs(jobs,path)
        return {'saved':len(jobs)}

    @routes.get('/settings/sources')
    def sources():
        return store.sources(path)

    @routes.post('/settings/sources', status_code=201)
    def add_source(body: SourceInput):
        url = str(body.url)
        if urlparse(url).username or urlparse(url).password:
            raise HTTPException(422, 'Source URLs must not contain credentials.')
        kind = classify(url)
        try:
            with db.connect(path) as conn:
                cursor = conn.execute('INSERT INTO job_sources(name,url,kind,enabled) VALUES (?,?,?,?)',
                                      (body.name, url, kind, int(kind != 'bookmark')))
                return dict(conn.execute('SELECT * FROM job_sources WHERE id=?', (cursor.lastrowid,)).fetchone())
        except sqlite3.IntegrityError:
            raise HTTPException(409, 'That source is already saved.') from None

    @routes.patch('/settings/sources/{source_id}')
    def toggle_source(source_id: int, body: SourceUpdate):
        with db.connect(path) as conn:
            row = conn.execute('SELECT * FROM job_sources WHERE id=?', (source_id,)).fetchone()
            if not row:
                raise HTTPException(404, 'Source not found.')
            if body.enabled and row['kind'] == 'bookmark':
                raise HTTPException(422, 'Automatic monitoring is not supported for this bookmark.')
            conn.execute('UPDATE job_sources SET enabled=? WHERE id=?', (body.enabled, source_id))
        return {'enabled': body.enabled}

    @routes.delete('/settings/sources/{source_id}', status_code=204)
    def delete_source(source_id: int):
        with db.connect(path) as conn:
            if not conn.execute('DELETE FROM job_sources WHERE id=?', (source_id,)).rowcount:
                raise HTTPException(404, 'Source not found.')
        return Response(status_code=204)

    @routes.post('/settings/sources/{source_id}/check')
    def check_source(source_id: int):
        source = next((s for s in store.sources(path) if s['id'] == source_id), None)
        if not source:
            raise HTTPException(404, 'Source not found.')
        try:
            jobs = load_source(source)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except (FetchError, ParseError) as exc:
            raise HTTPException(502, str(exc)) from exc
        prefs = store.preferences(path)
        telegram_store.ingest(path, source['url'], jobs, prefs=prefs)
        with db.connect(path) as conn:
            conn.execute("UPDATE job_sources SET last_checked=?,last_error='' WHERE id=?", (time.time(), source_id))
        return {'found': len(jobs), 'matching_preferences': sum(store.matches_preferences(j, prefs) for j in jobs)}

    @routes.get('/settings/telegram')
    def telegram_status():
        config = store.get(path, 'telegram', {})
        return {'configured': bool(config.get('token')), 'connected': bool(config.get('user_id')),
                'username': config.get('username', ''), 'user_id': config.get('user_id'),
                'last_error': store.get(path, 'telegram_error', ''),
                'heartbeat': store.get(path, 'telegram_heartbeat', 0)}

    @routes.post('/settings/telegram/connect')
    def connect_telegram(body: TelegramConnect):
        client = TelegramClient(body.token)
        try:
            bot = client.call('getMe')
            if client.call('getWebhookInfo').get('url'):
                raise HTTPException(409, 'This bot already has a webhook. Use a dedicated bot with long polling.')
        except TelegramError:
            raise HTTPException(502, 'Unable to connect. Check your bot token and network.') from None
        finally:
            client.close()
        code = secrets.token_urlsafe(24)
        # A new pairing starts with a fresh offset and no inherited recipient.
        config = {'token': body.token, 'username': bot['username'], 'pairing_code': code,
                  'pairing_expires': time.time() + 600, 'user_id': None, 'generation': secrets.token_hex(12), 'offset': 0}
        store.put(path, 'telegram', config)
        store.put(path, 'telegram_error', '')
        return {'url': f'https://t.me/{bot["username"]}?start={code}', 'expires_in': 600}

    @routes.delete('/settings/telegram', status_code=204)
    def disconnect_telegram():
        store.put(path, 'telegram', {})
        return Response(status_code=204)

    @routes.post('/jobs/{job_id}/prepare', status_code=202)
    def prepare_job(job_id: int, resume_id: int | None = None):
        if resume_id is not None and not any(r['id'] == resume_id for r in resume.list_resumes(path)):
            raise HTTPException(422, 'Resume not found.')
        job = db.get_job(job_id, path)
        if not job:
            raise HTTPException(404, 'Job not found.')
        if not valid_entry_url(job['application_url']):
            raise HTTPException(422, 'Use a public HTTPS application URL without embedded credentials.')
        profile = db.get_profile(path)
        if not profile or not profile.get('first_name') or not profile.get('last_name') or not profile.get('email'):
            raise HTTPException(422, 'Save your first name, last name, and email in Profile before preparing.')
        try:
            app_id = applications.queue_job(path, job_id, resume_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return applications.get(path, app_id)

    @routes.post('/applications/prepare-batch', status_code=202)
    def prepare_batch(body: PrepareBatch):
        results = []
        for job_id in dict.fromkeys(body.job_ids):
            try:
                results.append({'job_id': job_id, 'application': prepare_job(job_id, body.resume_id)})
            except HTTPException as exc:
                results.append({'job_id': job_id, 'error': exc.detail})
        return {'items': results}

    @routes.get('/saved-answers')
    def saved_answers():
        return answers.list_answers(path)

    @routes.delete('/saved-answers/{answer_id}', status_code=204)
    def delete_answer(answer_id: int):
        answers.forget(path, answer_id)
        return Response(status_code=204)

    @routes.get('/applications/{app_id}')
    def application(app_id: int):
        result = applications.get(path, app_id)
        if result is None:
            raise HTTPException(404, 'Application not found.')
        return result

    @routes.get('/applications/{app_id}/resume/file')
    def application_resume(app_id: int):
        if not applications.get(path, app_id):
            raise HTTPException(404,'Application not found.')
        directory = path.parent / 'applications' / str(app_id)
        files = [directory / ('resume'+suffix) for suffix in ('.pdf','.txt') if (directory / ('resume'+suffix)).exists()]
        if len(files) != 1:
            raise HTTPException(404,'No unique prepared resume file.')
        return FileResponse(files[0],filename=files[0].name)

    @routes.get('/applications/{app_id}/screenshot')
    def screenshot(app_id: int):
        result = applications.get(path, app_id)
        target = path.parent / 'applications' / str(app_id) / 'review.png'
        if result is None or not target.exists():
            raise HTTPException(404, 'No screenshot yet.')
        return FileResponse(target, media_type='image/png', headers={'Cache-Control': 'no-store'})

    def send_command(app_id, kind, payload):
        if applications.get(path, app_id) is None:
            raise HTTPException(404, 'Application not found.')
        try:
            applications.command(path, app_id, kind, payload)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {'accepted': True}

    @routes.post('/applications/{app_id}/edit', status_code=202)
    def edit(app_id: int, body: EditField):
        return send_command(app_id, 'edit', body.model_dump())

    @routes.post('/applications/{app_id}/navigate', status_code=202)
    def navigate(app_id: int, body: Navigate):
        return send_command(app_id, 'navigate', body.model_dump())

    @routes.post('/applications/{app_id}/suggest', status_code=202)
    def suggest(app_id: int, body: SuggestAnswer):
        return send_command(app_id, 'suggest', body.model_dump())

    @routes.post('/applications/{app_id}/submit', status_code=202)
    def submit(app_id: int, body: ReviewAction):
        """Explicit user approval of the current live revision; never automatically retried."""
        return send_command(app_id, 'submit', body.model_dump())

    @routes.post('/applications/{app_id}/options', status_code=202)
    def field_options(app_id: int, body: FieldOptions):
        return send_command(app_id, 'options', body.model_dump())

    @routes.post('/applications/{app_id}/cancel', status_code=202)
    def cancel(app_id: int):
        return send_command(app_id, 'cancel', {})

    @routes.post('/applications/{app_id}/retry', status_code=202)
    def retry(app_id: int):
        return send_command(app_id, 'retry', {})

    return routes
