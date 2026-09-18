"""Read public listing metadata without AI, logins, or application form actions."""

import http.client
import ipaddress
import json
import socket
import ssl
import time
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup
from src.jobs.models import Job
from src.jobs.fetch import FetchError
from src.jobs.sources import greenhouse_parts, careerpuck_parts, load_source

MAX_BYTES = 2_000_000


def validate_url(url):
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username
                or parsed.password or parsed.port not in (None, 443)):
            raise ValueError()
    except ValueError:
        raise ValueError('Use a public HTTPS application link without credentials.') from None
    return parsed


def _read_page(url):
    """Pin the connection to a checked public address; validate every redirect.

    TLS still verifies the original hostname. No cookies, profile data, browser
    sessions, environment proxies, or employer form requests are sent.
    """
    deadline = time.monotonic() + 20
    for _ in range(5):
        parsed = validate_url(url)
        addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise ValueError('Application links must point to public websites, not local or private services.')
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise FetchError('The listing took too long to respond.')
        timeout = min(8, remaining)
        context = ssl.create_default_context()
        connection = http.client.HTTPSConnection(parsed.hostname, timeout=timeout, context=context)
        try:
            sock = socket.create_connection((addresses[0][4][0], 443), timeout=timeout)
            try:
                connection.sock = context.wrap_socket(sock, server_hostname=parsed.hostname)
            except Exception:
                sock.close()
                raise
            target = urlunsplit(('', '', parsed.path or '/', parsed.query, ''))
            connection.request('GET', target, headers={'User-Agent':'ApplyAgent/1.0', 'Accept':'text/html,application/xhtml+xml'})
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader('Location')
                if not location:
                    raise FetchError('The listing returned an empty redirect.')
                url = urljoin(url, location)
                continue
            if response.status != 200:
                raise FetchError('The listing could not be read; it may require a login or block automated access.')
            if response.getheader('Content-Type', '').split(';')[0].strip() not in ('text/html', 'application/xhtml+xml'):
                raise FetchError('The link did not return a public HTML job listing.')
            content = bytearray()
            while True:
                if time.monotonic() > deadline:
                    raise FetchError('The listing took too long to respond.')
                chunk = response.read(65536)
                if not chunk:
                    return bytes(content), url
                content.extend(chunk)
                if len(content) > MAX_BYTES:
                    raise FetchError('The listing exceeds the download limit.')
        finally:
            connection.close()
    raise FetchError('The listing redirected too many times.')


def text(value, limit=500):
    return ' '.join(BeautifulSoup(value, 'html.parser').get_text(' ', strip=True).split())[:limit] if isinstance(value, str) else ''


def _postings(value):
    if isinstance(value, list):
        for child in value:
            yield from _postings(child)
    elif isinstance(value, dict):
        types = value.get('@type', [])
        if types == 'JobPosting' or (isinstance(types, list) and 'JobPosting' in types):
            yield value
        # Standard JSON-LD graphs and embedded mainEntity nodes.
        for key in ('@graph', 'mainEntity'):
            yield from _postings(value.get(key))


def parse_page(content, url):
    soup = BeautifulSoup(content, 'html.parser')
    candidates = []
    for script in soup.find_all('script', type='application/ld+json'):
        try:
            candidates.extend(_postings(json.loads(script.string or script.get_text())))
        except (ValueError, RecursionError):
            continue
    matched = [p for p in candidates if p.get('url') == url]
    posting = matched[0] if len(matched) == 1 else candidates[0] if len(candidates) == 1 else {}
    organization = posting.get('hiringOrganization') or {}
    company = text(organization.get('name'), 300) if isinstance(organization, dict) else ''
    title = text(posting.get('title'))
    locations = posting.get('jobLocation') or []
    if isinstance(locations, dict):
        locations = [locations]
    places = []
    if isinstance(locations, list):
        for location in locations:
            if not isinstance(location, dict):
                continue
            address = location.get('address', {})
            if isinstance(address, str):
                label = text(address)
            elif isinstance(address, dict):
                country = address.get('addressCountry', '')
                if isinstance(country, dict):
                    country = country.get('name', '')
                label = ', '.join(filter(None, (text(address.get('addressLocality')), text(address.get('addressRegion')), text(country))))
            else:
                label = ''
            label = label or text(location.get('name'))
            if label and label not in places:
                places.append(label)
    if posting.get('jobLocationType') == 'TELECOMMUTE':
        regions = posting.get('applicantLocationRequirements') or []
        if isinstance(regions, dict):
            regions = [regions]
        if isinstance(regions, list):
            for region in regions:
                name = text(region.get('name')) if isinstance(region, dict) else ''
                if name and name not in places:
                    places.append(name)
        places.insert(0, 'Remote')
    def meta(name):
        node = soup.find('meta', attrs={'property':name}) or soup.find('meta', attrs={'name':name})
        return text(node.get('content')) if node else ''
    # Do not treat one opening in a multi-job board as the requested job.
    if not title and len(candidates) <= 1:
        heading = soup.find('h1')
        title = meta('og:title') or (text(heading.get_text()) if heading else '') or (text(soup.title.get_text()) if soup.title else '')
    if title.lower().strip(' .!') in {'just a moment', 'access denied', 'sign in', 'log in', 'verify you are human'}:
        title = ''
    return {'company':company, 'title':title, 'location':'; '.join(places)[:500]}


def from_link(url):
    parsed = validate_url(url)
    details = {}
    warnings = []
    parts = greenhouse_parts(url) or careerpuck_parts(url)
    if parts and parts[1]:
        try:
            listing = load_source({'url':url, 'name':parts[0]})[0]
            details = {'company':listing.company, 'title':listing.title, 'location':listing.location}
        except (FetchError, ValueError, KeyError, IndexError, TypeError):
            pass
    if not details:
        try:
            content, final_url = _read_page(url)
            details = parse_page(content, final_url)
        except (FetchError, OSError, http.client.HTTPException) as exc:
            warnings.append(str(exc) if isinstance(exc, FetchError) else 'Could not read the public listing.')
    for key in ('company', 'title', 'location'):
        if not text(details.get(key)):
            warnings.append(f'{key.capitalize()} was not available from the listing.')
    job = Job(text(details.get('company'), 300) or parsed.hostname,
              text(details.get('title')) or 'Job details unavailable',
              text(details.get('location')) or 'Not specified', url, url)
    return job, warnings
