"""Learn manual desktop answers, with an audit trail and no credential capture."""
import json
import re
import time
from pathlib import Path
from src.applications.answers import question
from src.applications.takeover import clean_url
from src.db.database import connect
from src.setup import store as settings

EXCLUDED=re.compile(r'password|passcode|one.?time|verification.?code|security.?code|auth.?token|\botp\b|\bpin\b|signature|sign your|social security|\bssn\b|credit card|bank account|routing number',re.I)


class Learner:
    def __init__(self,worker):self.worker=worker

    def entry(self,page):
        for app_id,entry in self.worker.sessions.items():
            if page in entry.get('pages',[entry['session'].page]):return app_id,entry
        return None,None

    def enabled(self,page):
        _,entry=self.entry(page)
        return bool(entry and not entry.get('automating',True) and settings.preferences(self.worker.path).get('native_learning',True))

    def attach(self,context):
        context.expose_binding('__applyAgentLearningReady',lambda source:self.enabled(source['page']))
        context.expose_binding('__applyAgentLearn',self.receive)
        context.add_init_script(path=str(Path(__file__).with_name('learning.js')))

    def set_recording(self,entry,enabled):
        entry['automating']=not enabled
        for page in entry.get('pages',[entry['session'].page]):
            for frame in page.frames:
                try:frame.evaluate('(enabled)=>{if(window.__applyAgentSetRecording)window.__applyAgentSetRecording(enabled);else window.__applyAgentRecording=enabled}',enabled)
                except Exception:pass

    def receive(self,source,payload):
        if not self.enabled(source['page']) or not isinstance(payload,dict):return
        app_id,entry=self.entry(source['page'])
        label=payload.get('question');kind=payload.get('type');value=payload.get('value')
        if not isinstance(label,str) or not isinstance(value,str) or not label.strip() or len(label)>600 or len(value)>20000:return
        if kind not in ('text','email','tel','url','number','search','textarea','select','combobox','radio') or EXCLUDED.search(label):return
        from src.applications import store
        record=store.get(self.worker.path,app_id)
        if not record or record['status'] not in ('ready','takeover'):return
        company=record['company'] or ''
        specific=(company and company.casefold() in label.casefold()) or re.search(r'our company|this company|this (role|position)|why.*(join|work here)',label,re.I)
        scope=company.casefold() if specific else ''
        key=question(label);raw=json.dumps(value.strip())
        entry.setdefault('manual_answers',{})[key]=value.strip()
        with connect(self.worker.path) as conn:
            old=conn.execute('SELECT value FROM saved_answers WHERE question=? AND field_type=? AND company=?',(key,kind,scope)).fetchone()
            if old and old['value']==raw:return
            if not value.strip():
                if not old:return
                conn.execute('DELETE FROM saved_answers WHERE question=? AND field_type=? AND company=?',(key,kind,scope))
            else:
                conn.execute('''INSERT INTO saved_answers(question,field_type,company,value) VALUES (?,?,?,?)
                    ON CONFLICT(question,field_type,company) DO UPDATE SET value=excluded.value''',(key,kind,scope,raw))
            conn.execute('INSERT INTO learned_answers(application_id,question,field_type,value,source_url,created_at) VALUES (?,?,?,?,?,?)',
                         (app_id,key,kind,raw,clean_url(source['frame'].url),time.time()))
