"""Public Greenhouse discovery across occupations, plus the existing GitHub lists."""

import re
from urllib.parse import urlparse, parse_qs
import httpx
from src.jobs.fetch import FetchError, SOURCES, fetch_jobs
from src.jobs.parser import parse_jobs
from src.jobs.models import Job

GREENHOUSE_HOSTS = {'boards.greenhouse.io', 'job-boards.greenhouse.io'}


def greenhouse_parts(url):
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.hostname not in GREENHOUSE_HOSTS or parsed.username or parsed.port:
        return None
    if parsed.path.rstrip('/') == '/embed/job_app':
        query = parse_qs(parsed.query)
        board = query.get('for', [''])[0]
        job_id = query.get('token', [''])[0]
        if re.fullmatch(r'[A-Za-z0-9_-]+', board) and re.fullmatch(r'\d+', job_id):
            return board, job_id
        return None
    match = re.fullmatch(r'/([A-Za-z0-9_-]+)(?:/jobs/(\d+))?/?', parsed.path)
    return match.groups() if match else None


def careerpuck_parts(url):
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.hostname != 'app.careerpuck.com' or parsed.username or parsed.port:
        return None
    match = re.fullmatch(r'/job-board/([A-Za-z0-9_-]+)/job/(\d+)/?', parsed.path)
    return match.groups() if match else None


def classify(url):
    if careerpuck_parts(url):
        return 'careerpuck_job'
    parts = greenhouse_parts(url)
    if parts:
        return 'greenhouse_job' if parts[1] else 'greenhouse_board'
    if url in SOURCES.values():
        return 'github'
    return 'bookmark'


def get_json(url):
    try:
        # URLs are assembled from a fixed API host and validated board/job tokens.
        with httpx.stream('GET', url, timeout=20, follow_redirects=False) as response:
            response.raise_for_status()
            content = bytearray()
            for chunk in response.iter_bytes():
                content.extend(chunk)
                if len(content) > 8_000_000:
                    raise FetchError('Source exceeds the download limit.')
        import json
        return json.loads(content)
    except (httpx.HTTPError, ValueError) as exc:
        raise FetchError('Unable to read the public job board. Check the URL and try again.') from exc


def load_source(source):
    url = source['url']
    kind = classify(url)
    if kind == 'github':
        return parse_jobs(fetch_jobs(url), url)
    if kind == 'bookmark':
        raise ValueError('This site is saved as a bookmark; automatic discovery is not supported yet.')
    board, job_id = greenhouse_parts(url) or careerpuck_parts(url)
    base = f'https://boards-api.greenhouse.io/v1/boards/{board}'
    data = get_json(f'{base}/jobs/{job_id}' if job_id else f'{base}/jobs')
    if job_id and str(data.get('id')) != job_id:
        raise FetchError('The application source returned a different job. Import was stopped.')
    entries = [data] if job_id else data.get('jobs')
    if not isinstance(entries, list):
        raise FetchError('The job board returned an unexpected format.')
    jobs = []
    for entry in entries:
        if not isinstance(entry, dict) or not str(entry.get('id', '')).isdigit() or not entry.get('title'):
            raise FetchError('The job board contains an invalid listing.')
        # Use the hosted application even if the board API advertises a custom company page.
        apply_url = url if kind == 'careerpuck_job' else f'https://job-boards.greenhouse.io/{board}/jobs/{entry["id"]}'
        jobs.append(Job(entry.get('company_name') or source['name'], entry['title'],
                        (entry.get('location') or {}).get('name') or 'Not specified', apply_url, url))
    return jobs
