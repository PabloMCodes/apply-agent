"""Worker settings supplied through environment variables on any server."""

from dataclasses import dataclass, field
import os
from pathlib import Path

from src.db.database import DATABASE_PATH
from src.jobs.fetch import SOURCES


@dataclass
class Settings:
    token: str = field(repr=False)
    user_id: int
    db_path: Path = DATABASE_PATH
    sources: tuple[str, ...] = ('intern_usa',)
    interval: int = 900

    @classmethod
    def from_env(cls):
        token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
        if not token:
            raise ValueError('Set TELEGRAM_BOT_TOKEN before starting the worker.')
        user_id = int(os.environ.get('TELEGRAM_USER_ID', '0'))
        if user_id <= 0:
            raise ValueError('Set TELEGRAM_USER_ID to your numeric Telegram user ID.')
        sources = tuple(s.strip() for s in os.environ.get('JOB_SOURCES', 'intern_usa').split(','))
        if not sources or any(s not in SOURCES for s in sources):
            raise ValueError('JOB_SOURCES must contain supported source IDs.')
        interval = int(os.environ.get('JOB_POLL_SECONDS', '900'))
        if interval < 60:
            raise ValueError('JOB_POLL_SECONDS must be at least 60.')
        return cls(token, user_id, Path(os.environ.get('DATABASE_PATH', str(DATABASE_PATH))), sources, interval)
