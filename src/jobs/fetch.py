"""Fetch the four supported public SpeedyApply lists."""

import httpx

BASE_URL = 'https://raw.githubusercontent.com/speedyapply/2027-SWE-College-Jobs/main/'
SOURCES = {
    'intern_usa': BASE_URL + 'README.md',
    'new_grad_usa': BASE_URL + 'NEW_GRAD_USA.md',
    'intern_intl': BASE_URL + 'INTERN_INTL.md',
    'new_grad_intl': BASE_URL + 'NEW_GRAD_INTL.md',
}
MAX_BYTES = 5_000_000


class FetchError(Exception):
    """The remote list could not be downloaded."""


def fetch_jobs(source_url: str = SOURCES['intern_usa']) -> str:
    if source_url not in SOURCES.values():
        raise ValueError('Choose one of the supported source URLs.')
    try:
        # Fixed source URLs and no redirects keep ingestion scoped to GitHub.
        with httpx.stream('GET', source_url, timeout=20, follow_redirects=False) as response:
            response.raise_for_status()
            content = bytearray()
            for chunk in response.iter_bytes():
                content.extend(chunk)
                if len(content) > MAX_BYTES:
                    raise FetchError('Job list exceeds the 5 MB download limit.')
        return content.decode('utf-8')
    except (httpx.HTTPError, UnicodeDecodeError) as exc:
        raise FetchError('Could not download the GitHub job list. Try again later.') from exc
