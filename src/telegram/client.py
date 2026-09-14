"""Minimal Telegram HTTP client. Errors never include the secret-bearing URL."""

import httpx


class TelegramError(Exception):
    def __init__(self, code: int = 0, retry_after: int = 10):
        super().__init__(f'Telegram request failed (code {code}).')
        self.code = code
        self.retry_after = max(1, retry_after)


class TelegramClient:
    def __init__(self, token: str):
        self.base_url = f'https://api.telegram.org/bot{token}/'
        self.http = httpx.Client(timeout=40)

    def close(self):
        self.http.close()

    def call(self, method: str, **payload):
        try:
            response = self.http.post(self.base_url + method, json=payload)
            data = response.json()
        except (httpx.HTTPError, ValueError):
            raise TelegramError() from None
        if not data.get('ok'):
            raise TelegramError(data.get('error_code', response.status_code),
                                data.get('parameters', {}).get('retry_after', 10))
        return data['result']

    def updates(self, offset: int, timeout: int = 25):
        return self.call('getUpdates', offset=offset, timeout=timeout,
                         allowed_updates=['message', 'callback_query'])

    def send(self, chat_id: int, text: str, markup=None):
        payload = dict(chat_id=chat_id, text=text, link_preview_options={'is_disabled': True})
        if markup:
            payload['reply_markup'] = markup
        return self.call('sendMessage', **payload)

    def answer(self, callback_id: str, text: str):
        return self.call('answerCallbackQuery', callback_query_id=callback_id, text=text)
