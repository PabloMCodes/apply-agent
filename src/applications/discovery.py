"""Find the actual application behind a career page, before sending profile data."""

import time
from urllib.parse import urlparse

from src.jobs.sources import greenhouse_parts


class UnsupportedForm(ValueError):
    pass


def valid_entry_url(url):
    try:
        parsed = urlparse(url)
        return parsed.scheme == 'https' and bool(parsed.hostname) and not parsed.username and parsed.port in (None, 443)
    except ValueError:
        return False


# Require application evidence, not a newsletter/login/contact form. Mark exactly
# one candidate root so the common adapter cannot fill unrelated page inputs.
DETECT_STANDARD_FORM = r'''() => {
  const candidates = [...document.querySelectorAll('form')].filter(form => {
    if (!form.getClientRects().length || form.querySelector('input[type=password]')) return false;
    const inputs = [...form.querySelectorAll('input')];
    const email = inputs.some(i => i.type === 'email' || /email/i.test(i.name + i.id));
    const resume = inputs.some(i => i.type === 'file' && /resume|cv/i.test(i.name + i.id + [...(i.labels||[])].map(l=>l.innerText).join(' ')));
    const application = /application|apply for|apply to|submit application/i.test(form.innerText + form.id);
    const person = inputs.some(i => /first.?name|full.?name/i.test(i.name + i.id + [...(i.labels||[])].map(l=>l.innerText).join(' ')));
    return email && person && (resume || application);
  });
  document.querySelectorAll('[data-apply-agent-form]').forEach(e=>e.removeAttribute('data-apply-agent-form'));
  if (candidates.length !== 1) return false;
  candidates[0].setAttribute('data-apply-agent-form','');
  return true;
}'''


def discover(page, timeout=15):
    """Return a verified Greenhouse URL or a standard form on the existing page.

    Discovery is read-only: it never clicks a button, uploads a resume, or fills
    inputs. Only an explicit application link may be followed, at most once.
    """
    deadline = time.monotonic() + timeout
    followed_link = False
    while time.monotonic() < deadline:
        parts = greenhouse_parts(page.url)
        if parts and parts[1]:
            return 'greenhouse', page.url
        for frame in page.frames[1:]:
            parts = greenhouse_parts(frame.url)
            if parts and parts[1]:
                # Keep the discovered embed URL. A canonical hosted job URL may
                # redirect back to the company's wrapper, creating a loop.
                return 'greenhouse', frame.url
        if page.evaluate(DETECT_STANDARD_FORM):
            return 'standard', page.url
        if not followed_link:
            links = page.locator('a[href]').evaluate_all("""els => els.filter(el =>
                /^(apply|apply now|apply for this (job|position))$/i.test(el.innerText.trim()))
                .map(el=>el.href)""")
            targets = {link for link in links if valid_entry_url(link) and link.split('#')[0] != page.url.split('#')[0]}
            if len(targets) == 1:
                page.goto(targets.pop(), wait_until='domcontentloaded', timeout=20000)
                followed_link = True
                continue
        page.wait_for_timeout(500)
    raise UnsupportedForm('No supported application form was found. This site may use a multi-step flow, require login, or need an additional site adapter. No profile data was entered.')
