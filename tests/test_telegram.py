from importlib import import_module
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.db import database as db
from src.jobs.models import Job
from src.jobs.fetch import FetchError
from src.telegram import store
from src.telegram.client import TelegramClient, TelegramError
from src.telegram.config import Settings
from src.telegram.worker import Worker, worker_lock


@pytest.fixture
def path(tmp_path):
    path = tmp_path / 'jobs.sqlite3'
    db.initialize_database(path)
    store.initialize(path)
    return path


def job(number):
    return Job('Example', 'Engineer', 'Remote', f'https://example.com/{number}', 'source')


def alert(path):
    store.ingest(path, 'source', [job(1)])
    store.ingest(path, 'source', [job(1), job(2)])
    with db.connect(path) as conn:
        return conn.execute('SELECT id FROM telegram_alerts').fetchone()['id']


def callback(alert_id, action='approve', user=42, chat=42):
    return {'update_id': 100, 'callback_query': {
        'id': 'callback', 'data': f'{action}:{alert_id}', 'from': {'id': user},
        'message': {'chat': {'id': chat, 'type': 'private'}},
    }}


def test_baseline_and_restart_deduplication(path):
    store.ingest(path, 'source', [job(1)])
    assert 'Initial import' in store.pending(path)['text']
    store.mark_sent(path, store.pending(path)['id'])
    store.initialize(path)
    store.ingest(path, 'source', [job(1), job(2)])
    assert store.pending(path)['text'].startswith('New job')
    store.mark_sent(path, store.pending(path)['id'])
    store.ingest(path, 'source', [job(1), job(2)])
    assert store.pending(path) is None
    assert len(db.get_jobs(path)) == 2


def test_approval_is_durable_and_idempotent(path):
    alert_id = alert(path)
    worker = Worker(Settings('secret', 42, path), Mock())
    worker.handle_update(callback(alert_id))
    worker.handle_update(callback(alert_id))
    store.initialize(path)
    assert len(store.queue(path)) == 1
    assert store.queue(path)[0]['status'] == 'queued'
    assert db.get_job(2, path)['status'] == 'saved'
    with TestClient(create_app(path)) as client:
        assert len(client.get('/applications').json()) == 1


@pytest.mark.parametrize('user,chat', [(99, 42), (42, 99), (99, 99)])
def test_untrusted_sender_cannot_approve(path, user, chat):
    alert_id = alert(path)
    worker = Worker(Settings('secret', 42, path), Mock())
    worker.handle_update(callback(alert_id, user=user, chat=chat))
    assert store.queue(path) == []
    assert db.get_job(2, path)['status'] == 'new'


def test_skip_cannot_later_approve(path):
    alert_id = alert(path)
    assert store.decide(path, alert_id, 'skip') == 'Job skipped.'
    assert store.decide(path, alert_id, 'approve') == 'This alert was already handled.'
    assert store.queue(path) == []
    assert db.get_job(2, path)['status'] == 'skipped'


def test_deleted_job_does_not_approve_reused_id(path):
    alert_id = alert(path)
    db.delete_job(2, path)
    db.create_job(job(3), path)
    assert 'deleted' in store.decide(path, alert_id, 'approve')
    assert store.queue(path) == []


def test_failed_send_remains_pending(path):
    store.reply(path, 1, 'Hello')
    client = Mock()
    client.send.side_effect = TelegramError(429, 3)
    worker = Worker(Settings('secret', 42, path), client)
    with pytest.raises(TelegramError):
        worker.deliver_one()
    assert store.pending(path) is not None
    client.send.side_effect = None
    worker.deliver_one()
    assert store.pending(path) is None


def test_callback_ack_failure_does_not_lose_approval(path):
    alert_id = alert(path)
    client = Mock()
    client.answer.side_effect = TelegramError(400)
    Worker(Settings('secret', 42, path), client).handle_update(callback(alert_id))
    assert len(store.queue(path)) == 1


def test_command_replay_creates_one_reply(path):
    worker = Worker(Settings('secret', 42, path), Mock())
    update = {'update_id': 9, 'message': {'from': {'id': 42}, 'chat': {'id': 42, 'type': 'private'}, 'text': '/status'}}
    worker.handle_update(update)
    worker.handle_update(update)
    worker.deliver_one()
    assert store.pending(path) is None


def test_failed_baseline_retries_and_does_not_initialize(path, monkeypatch):
    module = import_module('src.telegram.worker')
    monkeypatch.setattr(module, 'fetch_jobs', Mock(side_effect=FetchError('Failed')))
    Worker(Settings('secret', 42, path), Mock()).ingest_due()
    assert store.state(path, 'baseline:' + module.SOURCES['intern_usa']) == ''
    assert store.state(path, 'last_error:intern_usa')
    assert float(store.state(path, 'next_poll:intern_usa')) > 0
    assert db.get_jobs(path) == []


def test_single_worker_lock(path):
    with worker_lock(path):
        with pytest.raises(ValueError, match='Another'):
            with worker_lock(path):
                pass


def test_config_requires_owner_and_supported_sources(monkeypatch):
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'secret')
    monkeypatch.setenv('TELEGRAM_USER_ID', '0')
    with pytest.raises(ValueError):
        Settings.from_env()
    monkeypatch.setenv('TELEGRAM_USER_ID', '42')
    monkeypatch.setenv('JOB_SOURCES', 'invalid')
    with pytest.raises(ValueError):
        Settings.from_env()
    monkeypatch.setenv('JOB_SOURCES', 'intern_usa')
    assert 'secret' not in repr(Settings.from_env())


def test_client_sanitizes_errors_and_honors_retry_after():
    client = TelegramClient('secret-token')
    client.http.close()
    client.http = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(429, json={'ok': False, 'error_code': 429, 'parameters': {'retry_after': 17}})))
    try:
        with pytest.raises(TelegramError) as error:
            client.send(42, 'Hello')
        assert error.value.retry_after == 17
        assert 'secret-token' not in str(error.value)
    finally:
        client.close()


def test_poll_offset_is_persisted_after_processing(path):
    from threading import Event
    stop = Event()
    client = Mock()
    client.call.return_value = {'url': ''}
    def updates(*args, **kwargs):
        stop.set()
        return [{'update_id': 123, 'message': {'from': {'id': 42}, 'chat': {'id': 42, 'type': 'private'}, 'text': '/help'}}]
    client.updates.side_effect = updates
    worker = Worker(Settings('secret', 42, path, sources=()), client, stop)
    worker.run()
    assert store.state(path, 'update_offset') == '124'
    assert store.pending(path) is not None
