"""Background services for one self-hosted installation, started with the web app."""

import logging
import secrets
from threading import Event, Thread
import time

from src.setup import store as settings
from src.jobs.sources import load_source
from src.telegram import store as telegram_store
from src.telegram.client import TelegramClient, TelegramError
from src.telegram.config import Settings
from src.telegram.worker import Worker, worker_lock
from src.applications.worker import BrowserWorker
from src.db.database import connect

logger = logging.getLogger(__name__)


def pair_update(path, config, update):
    message = update.get('message', {})
    chat = message.get('chat', {})
    user_id = message.get('from', {}).get('id')
    if config.get('user_id') or chat.get('type') != 'private' or chat.get('id') != user_id:
        return False
    expected = '/start ' + config.get('pairing_code', '')
    if (not config.get('pairing_code') or time.time() > config.get('pairing_expires', 0)
            or not secrets.compare_digest(message.get('text', ''), expected)):
        return False
    current = settings.get(path, 'telegram', {})
    if current.get('generation') != config.get('generation'):
        return False
    config.update(user_id=user_id, pairing_code='', pairing_expires=0)
    settings.put(path, 'telegram', config)
    telegram_store.reply(path, 'paired:' + config['generation'], 'Connected to Apply Agent. You will receive new-job alerts here. Use /status or /queue anytime.')
    return True


class Runtime:
    def __init__(self, path):
        self.path = path
        self.stop = Event()
        self.threads = []
        self.lock = None
        self.browser = BrowserWorker(self.path, self.stop)

    def start(self):
        self.lock = worker_lock(self.path)
        self.lock.__enter__()
        for name, target in [('sources', self.monitor), ('telegram', self.telegram),
                             ('browser', self.browser.run)]:
            thread = Thread(target=target, name='apply-agent-' + name, daemon=True)
            thread.start()
            self.threads.append(thread)

    def close(self):
        self.stop.set()
        for thread in self.threads:
            thread.join(timeout=45)
        if self.lock:
            self.lock.__exit__(None, None, None)

    def monitor(self):
        while not self.stop.is_set():
            try:
                prefs = settings.preferences(self.path)
                if prefs['monitoring_enabled']:
                    for source in settings.sources(self.path):
                        if self.stop.is_set():
                            break
                        if not source['enabled'] or source['kind'] == 'bookmark':
                            continue
                        if time.time() - (source['last_checked'] or 0) < prefs['poll_minutes'] * 60:
                            continue
                        error = ''
                        try:
                            jobs = load_source(source)
                            telegram_store.ingest(self.path, source['url'], jobs, prefs=prefs)
                        except Exception:
                            error = 'Could not fetch or parse this source. Check the URL or retry later.'
                        with connect(self.path) as conn:
                            conn.execute('UPDATE job_sources SET last_checked=?,last_error=? WHERE id=?',
                                         (time.time(), error, source['id']))
            except Exception as exc:
                logger.warning('Source monitoring paused after %s.', type(exc).__name__)
            self.stop.wait(5)

    def telegram(self):
        logging.getLogger('httpx').setLevel(logging.WARNING)
        logging.getLogger('httpcore').setLevel(logging.WARNING)
        client, generation = None, None
        try:
            while not self.stop.is_set():
                delay = 1
                try:
                    config = settings.get(self.path, 'telegram', {})
                    if not config.get('token'):
                        if client:
                            client.close()
                            client = None
                        generation = None
                        self.stop.wait(2)
                        continue
                    if generation != config['generation']:
                        if client:
                            client.close()
                        client = TelegramClient(config['token'])
                        generation = config['generation']
                    updates = client.updates(config.get('offset', 0), timeout=1)
                    if settings.get(self.path, 'telegram', {}).get('generation') != generation:
                        continue
                    for update in updates:
                        if not pair_update(self.path, config, update) and config.get('user_id'):
                            Worker(Settings(config['token'], config['user_id'], self.path, sources=()), client).handle_update(update)
                        config['offset'] = update['update_id'] + 1
                        if settings.get(self.path, 'telegram', {}).get('generation') == generation:
                            settings.put(self.path, 'telegram', config)
                    if config.get('user_id') and settings.get(self.path, 'telegram', {}).get('generation') == generation:
                        Worker(Settings(config['token'], config['user_id'], self.path, sources=()), client).deliver_one()
                    settings.put(self.path, 'telegram_error', '')
                    settings.put(self.path, 'telegram_heartbeat', time.time())
                except TelegramError as exc:
                    settings.put(self.path, 'telegram_error', f'Telegram unavailable (code {exc.code}). Retrying.')
                    delay = exc.retry_after
                except Exception as exc:
                    logger.warning('Telegram service paused after %s.', type(exc).__name__)
                    delay = 5
                self.stop.wait(delay)
        finally:
            if client:
                client.close()
