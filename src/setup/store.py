"""Persist settings next to the user's existing jobs, without product accounts."""

import json
from src.db.database import connect

DEFAULTS = {
    'review_slots': 5, 'roles': [], 'locations': [], 'exclude_keywords': [],
    'poll_minutes': 15, 'monitoring_enabled': False,
    'review_base_url': '',
}


def initialize(path):
    with connect(path) as conn:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS resumes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL, roles TEXT NOT NULL DEFAULT '',
                filename TEXT NOT NULL UNIQUE, original_name TEXT NOT NULL,
                text TEXT NOT NULL, size INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS application_resume (
                application_id INTEGER PRIMARY KEY, resume_id INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS local_settings (
                key TEXT PRIMARY KEY, value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS job_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL, url TEXT NOT NULL UNIQUE,
                kind TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
                last_checked REAL, last_error TEXT NOT NULL DEFAULT ''
            );
        ''')


def get(path, key, default=None):
    with connect(path) as conn:
        row = conn.execute('SELECT value FROM local_settings WHERE key=?', (key,)).fetchone()
        return json.loads(row['value']) if row else default


def put(path, key, value):
    with connect(path) as conn:
        conn.execute('INSERT INTO local_settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                     (key, json.dumps(value)))
    return value


def preferences(path):
    return {**DEFAULTS, **get(path, 'preferences', {})}


def sources(path):
    with connect(path) as conn:
        return [dict(row) for row in conn.execute('SELECT * FROM job_sources ORDER BY id').fetchall()]


def matches_preferences(job, prefs):
    """Transparent keyword filters, not AI fit scores. Empty lists allow all jobs."""
    title = job.title.casefold()
    location = job.location.casefold()
    text = f'{job.company} {job.title} {job.location}'.casefold()
    return (not prefs['roles'] or any(word.casefold() in title for word in prefs['roles'])) and (
        not prefs['locations'] or any(word.casefold() in location for word in prefs['locations'])) and not any(
        word.casefold() in text for word in prefs['exclude_keywords'])
