"""SQLite persistence without an ORM. Every operation closes its connection."""

from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
import json
import os
import sqlite3

from src.jobs.models import Job

DATABASE_PATH = Path(os.environ.get('DATABASE_PATH',
    str(Path(__file__).resolve().parents[2] / 'data' / 'jobs.sqlite3')))


@contextmanager
def connect(db_path: Path = DATABASE_PATH):
    connection = sqlite3.connect(db_path, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize_database(db_path: Path = DATABASE_PATH) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as connection:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY,
                company TEXT NOT NULL, title TEXT NOT NULL,
                location TEXT NOT NULL, application_url TEXT NOT NULL UNIQUE,
                source TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'new'
                    CHECK(status IN ('new','saved','skipped','ready_for_review','applied')),
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS ingestions (
                id INTEGER PRIMARY KEY, source_url TEXT NOT NULL,
                found INTEGER NOT NULL, inserted INTEGER NOT NULL,
                updated INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS profile (
                id INTEGER PRIMARY KEY CHECK(id = 1), content TEXT NOT NULL
            );
        """)
        columns = {row['name'] for row in connection.execute('PRAGMA table_info(jobs)')}
        if 'applied_at' not in columns:
            connection.execute('ALTER TABLE jobs ADD COLUMN applied_at TEXT')
        # All submission paths use the same timestamp rule. Old applied records
        # keep an unknown date rather than inventing a submission time.
        connection.executescript('''
            CREATE TRIGGER IF NOT EXISTS jobs_applied_date
            AFTER UPDATE OF status ON jobs WHEN OLD.status != NEW.status
            BEGIN
                UPDATE jobs SET applied_at = CASE WHEN NEW.status = 'applied'
                    THEN CURRENT_TIMESTAMP ELSE NULL END WHERE id = NEW.id;
            END;
        ''')


def _save_jobs(connection, jobs: list[Job]) -> tuple[int, int]:
    inserted = updated = 0
    for job in jobs:
        existing = connection.execute(
            'SELECT id FROM jobs WHERE application_url = ?', (job.application_url,)
        ).fetchone()
        # Refresh source fields without overwriting personal tracking data.
        connection.execute("""
            INSERT INTO jobs (company, title, location, application_url, source)
            VALUES (:company, :title, :location, :application_url, :source)
            ON CONFLICT(application_url) DO UPDATE SET
                company=excluded.company, title=excluded.title,
                location=excluded.location, source=excluded.source,
                updated_at=CURRENT_TIMESTAMP
        """, asdict(job))
        if existing:
            updated += 1
        else:
            inserted += 1
    return inserted, updated


def save_jobs(jobs: list[Job], db_path: Path = DATABASE_PATH) -> None:
    with connect(db_path) as connection:
        connection.execute('BEGIN IMMEDIATE')
        _save_jobs(connection, jobs)


def get_jobs(db_path: Path = DATABASE_PATH) -> list[Job]:
    with connect(db_path) as connection:
        return [Job(**dict(row)) for row in connection.execute(
            'SELECT company, title, location, application_url, source FROM jobs ORDER BY id'
        ).fetchall()]


def list_jobs(db_path: Path, limit: int, offset: int, q: str, status: str | None):
    where = "WHERE instr(lower(company || ' ' || title || ' ' || location), lower(?)) > 0"
    params = [q]
    if status:
        where += ' AND status = ?'
        params.append(status)
    with connect(db_path) as connection:
        total = connection.execute(f'SELECT count(*) FROM jobs {where}', params).fetchone()[0]
        rows = connection.execute(
            f'SELECT * FROM jobs {where} ORDER BY id DESC LIMIT ? OFFSET ?',
            [*params, limit, offset],
        ).fetchall()
    return dict(items=[dict(row) for row in rows], total=total, limit=limit, offset=offset)


def get_job(job_id: int, db_path: Path):
    with connect(db_path) as connection:
        row = connection.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        return dict(row) if row else None


def create_job(job: Job, db_path: Path):
    with connect(db_path) as connection:
        cursor = connection.execute("""
            INSERT INTO jobs (company, title, location, application_url, source)
            VALUES (:company, :title, :location, :application_url, :source)
        """, asdict(job))
        return dict(connection.execute('SELECT * FROM jobs WHERE id=?', (cursor.lastrowid,)).fetchone())


def update_tracking(job_id: int, status: str, notes: str, db_path: Path):
    with connect(db_path) as connection:
        connection.execute('BEGIN IMMEDIATE')
        previous = connection.execute('SELECT status FROM jobs WHERE id=?', (job_id,)).fetchone()
        connection.execute(
            'UPDATE jobs SET status=?, notes=?, updated_at=CURRENT_TIMESTAMP WHERE id=?',
            (status, notes, job_id),
        )
        row = connection.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        # Undo must permit a fresh run instead of reopening an already closed tab.
        if previous and previous['status'] == 'applied' and status != 'applied':
            if connection.execute("SELECT 1 FROM sqlite_master WHERE name='application_runs'").fetchone():
                connection.execute("""UPDATE application_runs SET status='cancelled',
                    message='Applied mark removed. Prepare again to open a fresh tab.', revision=revision+1
                    WHERE id IN (SELECT id FROM application_queue WHERE application_url=?)""",
                    (row['application_url'],))
        return dict(row) if row else None


def delete_job(job_id: int, db_path: Path) -> bool:
    with connect(db_path) as connection:
        return connection.execute('DELETE FROM jobs WHERE id=?', (job_id,)).rowcount > 0


def record_ingestion(jobs: list[Job], source_url: str, db_path: Path):
    # Commit the listings and success record together, or roll back both.
    with connect(db_path) as connection:
        connection.execute('BEGIN IMMEDIATE')
        inserted, updated = _save_jobs(connection, jobs)
        cursor = connection.execute(
            'INSERT INTO ingestions (source_url, found, inserted, updated) VALUES (?, ?, ?, ?)',
            (source_url, len(jobs), inserted, updated),
        )
        return dict(connection.execute('SELECT * FROM ingestions WHERE id=?', (cursor.lastrowid,)).fetchone())


def list_ingestions(db_path: Path, limit: int, offset: int):
    with connect(db_path) as connection:
        return [dict(row) for row in connection.execute(
            'SELECT * FROM ingestions ORDER BY id DESC LIMIT ? OFFSET ?', (limit, offset)
        ).fetchall()]


def get_ingestion(ingestion_id: int, db_path: Path):
    with connect(db_path) as connection:
        row = connection.execute('SELECT * FROM ingestions WHERE id=?', (ingestion_id,)).fetchone()
        return dict(row) if row else None


def get_profile(db_path: Path):
    with connect(db_path) as connection:
        row = connection.execute('SELECT content FROM profile WHERE id=1').fetchone()
        return json.loads(row['content']) if row else None


def save_profile(profile: dict, db_path: Path):
    with connect(db_path) as connection:
        connection.execute(
            'INSERT INTO profile (id, content) VALUES (1, ?) '
            'ON CONFLICT(id) DO UPDATE SET content=excluded.content',
            (json.dumps(profile),),
        )
    return profile


def delete_profile(db_path: Path):
    with connect(db_path) as connection:
        connection.execute('DELETE FROM profile WHERE id=1')
