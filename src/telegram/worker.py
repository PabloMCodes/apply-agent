"""One persistent worker: scheduled ingestion, Telegram replies, durable delivery."""

from contextlib import contextmanager
import fcntl
import json
import logging
import re
import signal
from threading import Event
import time

from src.db.database import initialize_database
from src.jobs.fetch import SOURCES, FetchError, fetch_jobs
from src.jobs.parser import ParseError, parse_jobs
from src.telegram.client import TelegramClient, TelegramError
from src.telegram.config import Settings
from src.telegram import store

logger = logging.getLogger(__name__)
HELP = ('Apply Agent is connected. New jobs will have Queue application and Skip buttons. '
        'Approval queues applications for form detection, preparation, and review. '
        'Nothing is submitted automatically. Commands: /status, /queue, /help.')


@contextmanager
def worker_lock(path):
    """Prevent two workers sharing a database from delivering the same outbox."""
    with open(str(path) + '.worker.lock', 'a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Another Telegram worker is already using this database.') from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


class Worker:
    def __init__(self, settings, client, stop=None):
        self.settings = settings
        self.client = client
        self.path = settings.db_path
        self.stop = stop or Event()

    def ingest_due(self):
        for source_id in self.settings.sources:
            if self.stop.is_set():
                return
            key = 'next_poll:' + source_id
            if time.time() < float(store.state(self.path, key, '0')):
                continue
            try:
                url = SOURCES[source_id]
                store.ingest(self.path, url, parse_jobs(fetch_jobs(url), url))
                store.set_state(self.path, 'last_success:' + source_id, int(time.time()))
                store.set_state(self.path, 'last_error:' + source_id, '')
                logger.info('Imported source %s.', source_id)
            except (FetchError, ParseError):
                # No traceback or downloaded content is needed in operational logs.
                store.set_state(self.path, 'last_error:' + source_id, 'Download or parsing failed')
                logger.warning('Source %s failed; retrying on the next scheduled check.', source_id)
            store.set_state(self.path, key, time.time() + self.settings.interval)

    def handle_update(self, update):
        callback = update.get('callback_query')
        message = callback.get('message', {}) if callback else update.get('message', {})
        sender = callback.get('from', {}) if callback else message.get('from', {})
        chat = message.get('chat', {})
        # Both identity and private destination must match, including forwarded buttons.
        if (sender.get('id') != self.settings.user_id or chat.get('id') != self.settings.user_id
                or chat.get('type') != 'private'):
            return
        if callback:
            match = re.fullmatch(r'(approve|skip):(\d{1,18})', callback.get('data', ''))
            result = store.decide(self.path, int(match[2]), match[1]) if match else 'Unknown action.'
            try:
                self.client.answer(callback['id'], result)
            except TelegramError:
                # The durable confirmation is delivered separately even if a button expires.
                pass
            return
        command = message.get('text', '').split(maxsplit=1)[0:1]
        command = command[0].split('@')[0] if command else ''
        if command == '/status':
            lines = ['Worker is online. Open Apply Agent to review prepared applications.']
            for source in self.settings.sources:
                last = store.state(self.path, 'last_success:' + source)
                display = time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(float(last))) if last else 'not yet'
                error = store.state(self.path, 'last_error:' + source)
                lines.append(f'{source}: last import {display}' + (f'; {error}' if error else ''))
            text = '\n'.join(lines)
        elif command == '/queue':
            items = store.queue(self.path, limit=10)
            text = 'Latest approved applications (open Apply Agent for live status):\n'
            text += '\n'.join(f'#{item["id"]}: {item["application_url"][:300]}' for item in items) or 'Queue is empty.'
        else:
            text = HELP
        store.reply(self.path, update['update_id'], text)

    def deliver_one(self):
        message = store.pending(self.path)
        if message:
            self.client.send(self.settings.user_id, message['text'],
                             json.loads(message['markup']) if message['markup'] else None)
            store.mark_sent(self.path, message['id'])
        return message is not None

    def run(self):
        # Fail explicitly if a prior webhook would conflict with long polling.
        if self.client.call('getWebhookInfo').get('url'):
            raise ValueError('This bot has a webhook configured. Use a dedicated polling bot or remove its webhook.')
        logger.info('Telegram worker started.')
        while not self.stop.is_set():
            try:
                self.ingest_due()
                self.deliver_one()
                offset = int(store.state(self.path, 'update_offset', '0'))
                updates = self.client.updates(offset, timeout=1 if store.pending(self.path) else 25)
                for update in updates:
                    self.handle_update(update)
                    # Commit progress only after the action/reply is durable.
                    store.set_state(self.path, 'update_offset', update['update_id'] + 1)
                store.set_state(self.path, 'heartbeat', int(time.time()))
                self.stop.wait(1)  # Keep personal-chat sends within Telegram's rate limit.
            except TelegramError as exc:
                logger.warning('Telegram unavailable (code %s); retrying.', exc.code)
                self.stop.wait(exc.retry_after)


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    # httpx's INFO request logs include the bot token in the URL.
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
    try:
        settings = Settings.from_env()
        initialize_database(settings.db_path)
        store.initialize(settings.db_path)
        stop = Event()
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        with worker_lock(settings.db_path):
            client = TelegramClient(settings.token)
            try:
                Worker(settings, client, stop).run()
            finally:
                client.close()
    except (ValueError, TelegramError) as exc:
        logger.error('%s', exc)
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
