"""Parse SpeedyApply Markdown tables with embedded HTML application links."""

import re
from bs4 import BeautifulSoup
from pydantic import HttpUrl, TypeAdapter, ValidationError
from src.jobs.models import Job

url_adapter = TypeAdapter(HttpUrl)


class ParseError(ValueError):
    """The source format changed or a listing is malformed."""


def clean_text(value: str) -> str:
    value = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', value)
    return BeautifulSoup(value, 'html.parser').get_text(' ', strip=True).strip('* ')


def parse_jobs(raw_listings: str, source: str = '') -> list[Job]:
    jobs = {}
    columns = None
    recognized = False
    previous_company = ''
    for line_number, line in enumerate(raw_listings.splitlines(), start=1):
        if not line.lstrip().startswith('|'):
            columns = None
            continue
        cells = [cell.strip() for cell in re.split(r'(?<!\\)\|', line.strip().strip('|'))]
        headers = [clean_text(cell).lower() for cell in cells]
        if all(name in headers for name in ('company', 'position', 'location', 'posting')):
            columns = {name: headers.index(name) for name in ('company', 'position', 'location', 'posting')}
            recognized = True
            previous_company = ''
            continue
        if columns is None or all(re.fullmatch(r':?-+:?', cell) for cell in cells):
            continue
        if len(cells) <= max(columns.values()):
            raise ParseError(f'Malformed job table row at line {line_number}.')
        company = clean_text(cells[columns['company']])
        if company in ('↳', '↪', '"', ''):
            company = previous_company
        else:
            previous_company = company
        posting = cells[columns['posting']]
        anchor = BeautifulSoup(posting, 'html.parser').find('a', href=True)
        markdown_link = re.search(r'\[[^\]]*\]\((https?://[^\s)]+)\)', posting)
        url = anchor['href'] if anchor else markdown_link.group(1) if markdown_link else None
        if not url:
            # Closed listings have no application link.
            if any(marker in posting.lower() for marker in ('🔒', 'closed', '❌')):
                continue
            raise ParseError(f'Missing application link at line {line_number}.')
        try:
            url = str(url_adapter.validate_python(url))
        except ValidationError as exc:
            raise ParseError(f'Invalid application URL at line {line_number}.') from exc
        title = clean_text(cells[columns['position']])
        location = clean_text(cells[columns['location']]) or 'Not specified'
        if not company or not title:
            raise ParseError(f'Missing job fields at line {line_number}.')
        jobs[url] = Job(company, title, location, url, source)
    if not recognized:
        raise ParseError('No supported job tables found; the source format may have changed.')
    return list(jobs.values())
