"""Durable alerts, decisions, and polling checkpoints in the existing SQLite DB."""

import json
from src.db.database import connect, _save_jobs


def initialize(path):
    with connect(path) as conn:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS telegram_state (
                key TEXT PRIMARY KEY, value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS telegram_seen (
                url TEXT PRIMARY KEY
            );
            CREATE TABLE IF NOT EXISTS telegram_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL UNIQUE,
                company TEXT NOT NULL, title TEXT NOT NULL, location TEXT NOT NULL,
                decision TEXT CHECK(decision IN ('approve','skip')),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS application_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                application_url TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'queued' CHECK(status='queued'),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS telegram_outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_key TEXT NOT NULL UNIQUE,
                text TEXT NOT NULL, markup TEXT,
                sent INTEGER NOT NULL DEFAULT 0
            );
        ''')


def state(path, key, default=''):
    with connect(path) as conn:
        row = conn.execute('SELECT value FROM telegram_state WHERE key=?', (key,)).fetchone()
        return row['value'] if row else default


def set_state(path, key, value):
    with connect(path) as conn:
        conn.execute('INSERT INTO telegram_state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                     (key, str(value)))


def enqueue(conn, key, text, markup=None):
    conn.execute('INSERT OR IGNORE INTO telegram_outbox(event_key,text,markup) VALUES (?,?,?)',
                 (key, text, json.dumps(markup) if markup else None))


def ingest(path, source, jobs, prefs=None):
    with connect(path) as conn:
        conn.execute('BEGIN IMMEDIATE')
        baseline = conn.execute('SELECT 1 FROM telegram_state WHERE key=?', ('baseline:' + source,)).fetchone() is None
        inserted, updated = _save_jobs(conn, jobs)
        conn.execute('INSERT INTO ingestions(source_url,found,inserted,updated) VALUES (?,?,?,?)',
                     (source, len(jobs), inserted, updated))
        for job in jobs:
            is_new = conn.execute('INSERT OR IGNORE INTO telegram_seen VALUES (?)', (job.application_url,)).rowcount
            if baseline or not is_new:
                continue
            if prefs is not None:
                from src.setup.store import matches_preferences
                if not matches_preferences(job, prefs):
                    continue
            tracked = conn.execute('SELECT status FROM jobs WHERE application_url=?', (job.application_url,)).fetchone()
            if tracked['status'] != 'new':
                continue
            cursor = conn.execute('INSERT INTO telegram_alerts(url,company,title,location) VALUES (?,?,?,?)',
                                  (job.application_url, job.company, job.title, job.location))
            alert_id = cursor.lastrowid
            markup = {'inline_keyboard': [[
                {'text': 'Queue application', 'callback_data': f'approve:{alert_id}'},
                {'text': 'Skip', 'callback_data': f'skip:{alert_id}'},
            ]]}
            text = f'New job #{alert_id}\n{job.company[:300]} — {job.title[:500]}\n{job.location[:500]}\n{job.application_url[:2000]}'
            enqueue(conn, f'alert:{alert_id}', text, markup)
        if baseline:
            conn.execute('INSERT INTO telegram_state VALUES (?,?)', ('baseline:' + source, '1'))
            enqueue(conn, 'baseline:' + source,
                    f'Initial import complete: {len(jobs)} jobs from {source}. Future new listings will trigger alerts.')


def decide(path, alert_id, action):
    if action not in ('approve', 'skip'):
        return 'Unknown action.'
    with connect(path) as conn:
        conn.execute('BEGIN IMMEDIATE')
        alert = conn.execute('SELECT * FROM telegram_alerts WHERE id=?', (alert_id,)).fetchone()
        if alert is None:
            return 'This alert no longer exists.'
        if alert['decision']:
            return 'This alert was already handled.'
        job = conn.execute('SELECT * FROM jobs WHERE application_url=?', (alert['url'],)).fetchone()
        if job is None or job['status'] in ('skipped', 'applied'):
            return 'This job was deleted, skipped, or already applied to.'
        conn.execute('UPDATE telegram_alerts SET decision=? WHERE id=?', (action, alert_id))
        if action == 'approve':
            conn.execute('INSERT OR IGNORE INTO application_queue(application_url) VALUES (?)', (alert['url'],))
            conn.execute("UPDATE jobs SET status='saved', updated_at=CURRENT_TIMESTAMP WHERE application_url=?", (alert['url'],))
            text = f'Queued: {alert["company"][:300]} — {alert["title"][:500]}. Apply Agent will detect the application form and prepare supported fields for review. Nothing has been submitted.'
        else:
            conn.execute("UPDATE jobs SET status='skipped', updated_at=CURRENT_TIMESTAMP WHERE application_url=?", (alert['url'],))
            text = f'Skipped: {alert["company"][:300]} — {alert["title"][:500]}.'
        enqueue(conn, f'decision:{alert_id}', text)
        return 'Application queued.' if action == 'approve' else 'Job skipped.'


def reply(path, update_id, text):
    with connect(path) as conn:
        enqueue(conn, f'reply:{update_id}', text)


def pending(path):
    with connect(path) as conn:
        row = conn.execute('SELECT * FROM telegram_outbox WHERE sent=0 ORDER BY id LIMIT 1').fetchone()
        return dict(row) if row else None


def mark_sent(path, message_id):
    with connect(path) as conn:
        conn.execute('UPDATE telegram_outbox SET sent=1 WHERE id=?', (message_id,))


def queue(path, limit=50, offset=0):
    with connect(path) as conn:
        return [dict(row) for row in conn.execute(
            'SELECT * FROM application_queue ORDER BY id DESC LIMIT ? OFFSET ?', (limit, offset)).fetchall()]
