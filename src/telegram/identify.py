"""Print recent private-chat sender IDs for setup, without sending any messages."""

import os
from src.telegram.client import TelegramClient, TelegramError


def main():
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
    if not token:
        raise SystemExit('Set TELEGRAM_BOT_TOKEN first.')
    client = TelegramClient(token)
    try:
        updates = client.updates(0, timeout=0)
        users = {update['message']['from']['id'] for update in updates
                 if update.get('message', {}).get('chat', {}).get('type') == 'private'}
        for user_id in sorted(users):
            print(f'Private sender ID: {user_id}')
        if not users:
            print('Open your bot in Telegram, send /start, and run this command again.')
    except TelegramError as exc:
        raise SystemExit(str(exc)) from None
    finally:
        client.close()


if __name__ == '__main__':
    main()
