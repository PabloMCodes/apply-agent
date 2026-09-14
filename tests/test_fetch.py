from importlib import import_module

import httpx
import pytest

from src.jobs.fetch import FetchError, SOURCES, fetch_jobs

fetch_module = import_module('src.jobs.fetch')


def test_fetch_returns_text(monkeypatch):
    def stream(*args, **kwargs):
        return httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text='job table')
        )).stream(*args, **kwargs)
    monkeypatch.setattr(fetch_module.httpx, 'stream', stream)
    assert fetch_jobs() == 'job table'


def test_rejects_unknown_source():
    with pytest.raises(ValueError):
        fetch_jobs('http://localhost/private')


@pytest.mark.parametrize('status', [302, 404, 500])
def test_upstream_errors_and_redirects(monkeypatch, status):
    def stream(*args, **kwargs):
        return httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(status, headers={'location': 'http://localhost'})
        )).stream(*args, **kwargs)
    monkeypatch.setattr(fetch_module.httpx, 'stream', stream)
    with pytest.raises(FetchError):
        fetch_jobs(SOURCES['intern_usa'])


def test_size_limit(monkeypatch):
    def stream(*args, **kwargs):
        return httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b'12345')
        )).stream(*args, **kwargs)
    monkeypatch.setattr(fetch_module.httpx, 'stream', stream)
    monkeypatch.setattr(fetch_module, 'MAX_BYTES', 4)
    with pytest.raises(FetchError, match='limit'):
        fetch_jobs()
