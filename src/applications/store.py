"""Browser runs extend the existing queue without rewriting users' old data."""

import json
import time
from src.db.database import connect
from src.telegram.store import enqueue
from src.setup.store import preferences

ACTIVE = ('preparing', 'ready', 'takeover', 'submitting')


def initialize(path):
    with connect(path) as conn:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS saved_answers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question TEXT NOT NULL, field_type TEXT NOT NULL,
                company TEXT NOT NULL, value TEXT NOT NULL,
                UNIQUE(question, field_type, company)
            );
            CREATE TABLE IF NOT EXISTS learned_answers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                application_id INTEGER NOT NULL, question TEXT NOT NULL,
                field_type TEXT NOT NULL, value TEXT NOT NULL,
                source_url TEXT NOT NULL, created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS application_runs (
                id INTEGER PRIMARY KEY,
                status TEXT NOT NULL,
                revision INTEGER NOT NULL DEFAULT 0,
                snapshot TEXT NOT NULL DEFAULT '{}',
                message TEXT NOT NULL DEFAULT '',
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS browser_commands (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                application_id INTEGER NOT NULL,
                kind TEXT NOT NULL, payload TEXT NOT NULL,
                done INTEGER NOT NULL DEFAULT 0
            );
        ''')


def queue_job(path, job_id, resume_id=None):
    with connect(path) as conn:
        conn.execute('BEGIN IMMEDIATE')
        job = conn.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not job:
            raise LookupError('Job not found.')
        if job['status'] in ('applied', 'skipped'):
            raise ValueError('This job is marked applied or skipped. Update its status first.')
        conn.execute('INSERT OR IGNORE INTO application_queue(application_url) VALUES (?)', (job['application_url'],))
        row = conn.execute('SELECT id FROM application_queue WHERE application_url=?', (job['application_url'],)).fetchone()
        if resume_id is not None:
            existing = conn.execute('SELECT status FROM application_runs WHERE id=?',(row['id'],)).fetchone()
            if existing and existing['status'] in ('preparing','ready','takeover','submitting','submitted','submission_unknown'):
                raise ValueError('Close this review before changing its resume.')
            conn.execute('INSERT INTO application_resume VALUES (?,?) ON CONFLICT(application_id) DO UPDATE SET resume_id=excluded.resume_id',(row['id'],resume_id))
        existing = conn.execute('SELECT status FROM application_runs WHERE id=?',(row['id'],)).fetchone()
        if existing and existing['status'] in ('error','expired','unsupported','needs_attention','cancelled'):
            conn.execute('DELETE FROM application_runs WHERE id=?',(row['id'],))
            conn.execute('UPDATE browser_commands SET done=1 WHERE application_id=?',(row['id'],))
        return row['id']


def get(path, app_id):
    with connect(path) as conn:
        row = conn.execute('''SELECT q.id,q.application_url,q.created_at,
            COALESCE(r.status, 'queued') AS status, COALESCE(r.revision,0) AS revision,
            COALESCE(r.snapshot,'{}') AS snapshot, COALESCE(r.message,'') AS message,
            j.id AS job_id,j.company,j.title,j.status AS job_status,j.applied_at FROM application_queue q
            LEFT JOIN application_runs r ON r.id=q.id
            LEFT JOIN jobs j ON j.application_url=q.application_url WHERE q.id=?''', (app_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        if result['job_status'] == 'applied' and result['status'] != 'submitted':
            result['status'] = 'submitted'
            result['message'] = 'Recorded as applied. This job is excluded from preparation.'
        result['snapshot'] = json.loads(result['snapshot'])
        return result


def list_runs(path, limit=50, offset=0, active_only=False):
    with connect(path) as conn:
        where = "WHERE NOT EXISTS (SELECT 1 FROM jobs j WHERE j.application_url=q.application_url AND j.status='applied')" if active_only else ''
        ids = [row['id'] for row in conn.execute(f'SELECT q.id FROM application_queue q {where} ORDER BY q.id DESC LIMIT ? OFFSET ?', (limit, offset))]
    return [get(path, app_id) for app_id in ids]


def set_run(path, app_id, status, snapshot=None, message='', notify=False):
    with connect(path) as conn:
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('''INSERT INTO application_runs(id,status,snapshot,message,updated_at,revision)
            VALUES (?,?,?,?,?,1) ON CONFLICT(id) DO UPDATE SET
            status=excluded.status,snapshot=excluded.snapshot,message=excluded.message,
            updated_at=excluded.updated_at,revision=application_runs.revision+1''',
            (app_id, status, json.dumps(snapshot or {}), message, time.time()))
        if status in ('ready', 'submitted'):
            conn.execute("UPDATE jobs SET status=?,updated_at=CURRENT_TIMESTAMP WHERE status != 'applied' AND application_url=(SELECT application_url FROM application_queue WHERE id=?)",
                         ('applied' if status == 'submitted' else 'ready_for_review', app_id))
        if notify:
            revision = conn.execute('SELECT revision FROM application_runs WHERE id=?', (app_id,)).fetchone()[0]
            # Use a user's private installation URL, never a public session token.
            prefs_row = conn.execute("SELECT value FROM local_settings WHERE key='preferences'").fetchone()
            base = json.loads(prefs_row['value']).get('review_base_url', '') if prefs_row else ''
            link = f'\n{base}/#applications/{app_id}' if base else '\nOpen Apply Agent → Applications to review.'
            enqueue(conn, f'run:{app_id}:{revision}', f'Application #{app_id}: {message}' + link)


def next_queued(path):
    with connect(path) as conn:
        row = conn.execute('''SELECT q.id FROM application_queue q
            LEFT JOIN application_runs r ON r.id=q.id WHERE r.id IS NULL ORDER BY q.id LIMIT 1''').fetchone()
        return row['id'] if row else None


def command(path, app_id, kind, payload):
    with connect(path) as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT * FROM application_runs WHERE id=?', (app_id,)).fetchone()
        if row is None:
            raise ValueError('The browser has not prepared this application yet.')
        if kind == 'retry':
            if row['status'] not in ('error', 'expired', 'unsupported', 'needs_attention', 'cancelled'):
                raise ValueError('This application cannot be restarted in its current state.')
        elif kind == 'cancel':
            if row['status'] not in ('ready', 'preparing', 'takeover'):
                raise ValueError('This application cannot be closed in its current state.')
        elif row['status'] != 'ready' or row['revision'] != payload.get('revision'):
            raise ValueError('The application changed. Reload it and review the current version.')
        if conn.execute('SELECT 1 FROM browser_commands WHERE application_id=? AND done=0', (app_id,)).fetchone():
            raise ValueError('A browser action is still running. Wait for it to finish.')
        conn.execute('INSERT INTO browser_commands(application_id,kind,payload) VALUES (?,?,?)',
                     (app_id, kind, json.dumps(payload)))


def next_command(path):
    with connect(path) as conn:
        row = conn.execute('SELECT * FROM browser_commands WHERE done=0 ORDER BY id LIMIT 1').fetchone()
        return dict(row) if row else None


def complete_command(path, command_id):
    with connect(path) as conn:
        conn.execute('UPDATE browser_commands SET done=1 WHERE id=?', (command_id,))


def recover(path):
    # Never replay a submit after a process crash; its remote outcome may be unknown.
    with connect(path) as conn:
        conn.execute("UPDATE application_runs SET status=CASE WHEN status='submitting' THEN 'submission_unknown' ELSE 'expired' END, message='Browser session ended. Review the outcome before continuing.', revision=revision+1 WHERE status IN ('preparing','ready','takeover','submitting')")
        conn.execute('UPDATE browser_commands SET done=1 WHERE done=0')
