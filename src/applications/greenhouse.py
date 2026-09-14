"""Conservative Greenhouse form adapter. Known facts only; no guessed answers."""

import hashlib
import json
import re
from urllib.parse import urlparse
from src.jobs.sources import greenhouse_parts

# IDs live in the browser DOM for this session and are never CSS supplied by a user.
SCAN = r'''() => {
  window.__applyAgentId = window.__applyAgentId || 0;
  return [...document.querySelectorAll('input,textarea,select')].filter(el =>
    el.type !== 'hidden' && !['submit','button','reset'].includes(el.type) &&
    el.getAttribute('aria-hidden') !== 'true' && !el.closest('[aria-hidden="true"]') &&
    (el.type === 'file' || el.getClientRects().length > 0) && !el.disabled
  ).map(el => {
    if (!el.dataset.applyAgentId) el.dataset.applyAgentId = 'f' + (++window.__applyAgentId);
    let label = [...(el.labels || [])].map(l => {
      const copy=l.cloneNode(true);
      copy.querySelectorAll('input,select,textarea,button').forEach(node=>node.remove());
      return copy.textContent;
    }).join(' ').trim();
    if (!label) label = el.getAttribute('aria-label') || el.name || el.id || 'Unlabelled field';
    const credential = el.type === 'password' || /password|passcode|one.?time|verification.?code|security.?code|auth.?token|otp/i.test(label+' '+el.name+' '+el.id+' '+el.autocomplete);
    const custom = el.getAttribute('role') === 'combobox' || el.getAttribute('aria-autocomplete') === 'list';
    const select = el.closest('.select');
    const knownCombo = custom && select && !select.querySelector('.select__multi-value');
    const type = custom ? (knownCombo ? 'combobox' : 'custom') : el.tagName === 'SELECT' ? 'select' : el.type || 'text';
    if (el.type === 'file' && el.id === 'resume') label = 'Resume';
    const group=el.closest('fieldset,[role=radiogroup]');
    const groupLabel=el.type==='radio' ? (group?.querySelector('legend')?.textContent || group?.getAttribute('aria-label') || '') : '';
    return {question_label:groupLabel.trim().slice(0,600),id:el.dataset.applyAgentId, label:label.slice(0,600), name:el.name || el.id,
      type, required:el.required || el.getAttribute('aria-required') === 'true' || label.includes('*'),
      credential, value: credential ? '' : type === 'combobox' ? (select.querySelector('.select__single-value')?.textContent || '') :
        type === 'file' ? [...el.files].map(f=>f.name).join(', ') :
        ['checkbox','radio'].includes(type) ? el.checked : el.value,
      options:el.tagName === 'SELECT' ? [...el.options].map(o=>({value:o.value,label:o.text})) : [],
      valid: el.validity.valid, validation_message: el.validationMessage || '',
      supported: !credential && (!custom || knownCombo) && !['password','file'].includes(type)};
  });
}'''


def fingerprint(snapshot):
    # Review refers to the actual visible values and form shape, not just a queue entry.
    payload = {key: snapshot[key] for key in ('url', 'fields', 'blockers')}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


class GreenhouseSession:
    scan_script = SCAN

    def accepted_frame(self, frame, index):
        return index == 0 or bool(greenhouse_parts(frame.url))

    def __init__(self, page):
        self.page = page
        self.frames = {}
        self.resume_uploaded = False
        self.options = {}
        self.option_queries = {}
        self.reused_answers = {}
        self.field_reviews = {}
        self.page_number = 1
        self.history = {}
        self.page_states = {}

    def snapshot(self):
        fields, blockers = [], []
        self.frames = {}
        for index, frame in enumerate(self.page.frames):
            if not self.accepted_frame(frame, index):
                # Ignore analytics/challenge frames; they cannot supply our application fields.
                continue
            self.frames[str(index)] = frame
            for field in frame.evaluate(self.scan_script):
                field['id'] = f'{index}:{field["id"]}'
                if field['type'] == 'combobox':
                    field['options'] = self.options.get(field['id'], [])
                fields.append(field)
        if not fields and not self.history:
            blockers.append('No supported application form found. The site may require login or have changed.')
        for field in fields:
            radio_group_answered = field['type'] == 'radio' and any(
                other['name'] == field['name'] and other['value'] for other in fields if other['type'] == 'radio')
            if field['required'] and not field['value'] and not radio_group_answered:
                blockers.append(f'Complete: {field["label"]}')
            if field['value'] and not field['valid']:
                blockers.append(f'Check {field["label"]}: {field["validation_message"]}')
            if field['type'] in ('custom', 'password') and field['required']:
                blockers.append(f'This control requires manual completion on the employer site: {field["label"]}')
        if self.page.locator('iframe[src*="recaptcha"], iframe[src*="hcaptcha"]').count():
            for frame in self.page.locator('iframe[src*="recaptcha"], iframe[src*="hcaptcha"]').all():
                if frame.is_visible():
                    blockers.append('A CAPTCHA needs manual completion on the employer site.')
                    break
        for field in fields:
            review = self.field_reviews.get(field['id'])
            if review:
                field['review'] = review
                if review.get('pending'):
                    blockers.append(f'Review answer: {field["label"]}')
            else:
                field['review'] = {'status':'previously_confirmed' if field['value'] else 'needs_answer',
                    'source':'Existing form value — verify it' if field['value'] else 'No matching confirmed information', 'pending':False}
            if field['id'] in self.reused_answers:
                field['saved_answer_id'] = self.reused_answers[field['id']]
                field['review'] = {'status':'previously_confirmed','source':f"Saved answer #{self.reused_answers[field['id']]}",'pending':True}
                blockers.append(f'Confirm remembered answer: {field["label"]}')
        submit = self.submit_button()
        if submit is None:
            blockers.append('A unique Submit application button was not found.')
        elif not submit.is_enabled():
            blockers.append('The employer has not enabled submission yet. Check required fields and validation.')
        for number, previous in self.history.items():
            if number != self.page_number and any(f.get('saved_answer_id') or f.get('review',{}).get('pending') for f in previous['fields']):
                blockers.append(f'Review remembered or suggested answers on page {number}. Use Previous page.')
        next_button = self.navigation_button('next')
        if next_button is not None:
            blockers.append('Continue to the final application page before submitting.')
        result = {'page_number':self.page_number, 'can_next':next_button is not None, 'can_back':self.navigation_button('back') is not None,
                  'url': self.page.url, 'fields': fields, 'blockers': list(dict.fromkeys(blockers)),
                  'can_submit': not blockers}
        result['fingerprint'] = fingerprint(result)
        return result

    def locator(self, field_id):
        frame_id, node_id = field_id.split(':', 1)
        if frame_id not in self.frames or not re.fullmatch(r'f\d+', node_id):
            raise ValueError('This field is no longer available.')
        return self.frames[frame_id].locator(f'[data-apply-agent-id="{node_id}"]')

    def autofill(self, profile, resume_path=None):
        snapshot = self.snapshot()
        mapping = {
            'first name': 'first_name', 'first_name': 'first_name',
            'last name': 'last_name', 'last_name': 'last_name',
            'email': 'email', 'email address': 'email', 'phone': 'phone',
            'phone number': 'phone', 'linkedin profile': 'linkedin',
            'linkedin': 'linkedin', 'github': 'github', 'github url': 'github', 'website': 'website', 'portfolio': 'website',
            'full name': 'name', 'full_name': 'name',
        }
        for field in snapshot['fields']:
            label = re.sub(r'\s+', ' ', field['label'].lower().replace('*', '')).strip()
            key = mapping.get(label) or mapping.get(field['name'])
            if key and profile.get(key) and field['supported'] and field['type'] not in ('checkbox','radio','select','combobox'):
                if not field['value']:
                    self.locator(field['id']).fill(profile[key])
                    self.field_reviews[field['id']] = {'status':'previously_confirmed','source':f'Profile: {key}', 'pending':False}
            elif field['type'] == 'file' and re.search(r'resume|\bcv\b', (label + ' ' + field['name']).lower()) and resume_path and not field['value']:
                self.locator(field['id']).set_input_files(str(resume_path))
                self.resume_uploaded = True

    def edit(self, field_id, value):
        snapshot = self.snapshot()
        field = next((f for f in snapshot['fields'] if f['id'] == field_id), None)
        if not field or not field['supported']:
            raise ValueError('This field needs manual completion on the employer site.')
        locator = self.locator(field_id)
        if field['type'] in ('checkbox','radio'):
            if not isinstance(value, bool):
                raise ValueError('Choose checked or unchecked.')
            locator.set_checked(value)
        elif field['type'] == 'select':
            if value not in [option['value'] for option in field['options']]:
                raise ValueError('Choose one of the available options.')
            locator.select_option(value)
        elif field['type'] == 'combobox':
            if value not in [option['value'] for option in self.options.get(field_id, [])]:
                raise ValueError('Find choices first, then choose an option supplied by the employer.')
            self.find_options(field_id, self.option_queries.get(field_id, ''), close=False)
            frame = self.frames[field_id.split(':')[0]]
            frame.get_by_role('option', name=str(value), exact=True).click(timeout=5000)
        else:
            locator.fill(str(value))

    def find_options(self, field_id, query='', close=True):
        snapshot = self.snapshot()
        field = next((f for f in snapshot['fields'] if f['id'] == field_id), None)
        if not field or field['type'] != 'combobox':
            raise ValueError('This is not a supported dropdown.')
        locator = self.locator(field_id)
        locator.click()
        if locator.is_editable():
            locator.fill(query)
        locator.press('ArrowDown')
        frame = self.frames[field_id.split(':')[0]]
        try:
            frame.get_by_role('option').first.wait_for(timeout=5000)
            values = frame.get_by_role('option').all_text_contents()
            self.options[field_id] = [{'value': ' '.join(value.split()), 'label': ' '.join(value.split())} for value in values[:500]]
            self.option_queries[field_id] = query
        finally:
            # Leave the saved selection intact and dismiss the menu after reading choices.
            if close:
                locator.press('Escape')
        return self.options.get(field_id, [])

    def navigation_button(self, direction):
        pattern = r'^(?:next|next step|continue|save and continue|review|review application)$' if direction == 'next' else r'^(?:back|previous|previous step)$'
        buttons = []
        for frame in self.frames.values():
            buttons.extend(b for b in frame.get_by_role('button',name=re.compile(pattern,re.I)).all() if b.is_visible() and b.is_enabled())
        return buttons[0] if len(buttons) == 1 else None

    def navigate(self, direction):
        before = self.snapshot()
        if direction == 'next':
            # Pending suggestions may be reviewed later, but required/invalid controls cannot be skipped.
            if any(b.startswith(('Complete:', 'Check ', 'This control', 'A CAPTCHA')) for b in before['blockers']):
                raise ValueError('Complete required answers on this page before continuing.')
            if self.submit_button() is not None:
                raise ValueError('Already at the final page. Review before submitting.')
        button = self.navigation_button(direction)
        if button is None:
            raise ValueError('No unique supported navigation button was found.')
        if direction == 'back' and self.page_number <= 1:
            raise ValueError('No earlier recorded page.')
        self.history[self.page_number] = before
        self.page_states[self.page_number] = (dict(self.field_reviews),dict(self.reused_answers))
        old_shape = [(f['label'],f['type']) for f in before['fields']]
        button.click(timeout=15000)
        self.page.wait_for_timeout(800)
        self.refresh_root()
        after = self.snapshot()
        if after['url'] == before['url'] and [(f['label'],f['type']) for f in after['fields']] == old_shape:
            raise ValueError('The page did not advance. Check employer validation or complete this step manually.')
        self.page_number += 1 if direction == 'next' else -1
        self.field_reviews, self.reused_answers = self.page_states.get(self.page_number, ({},{}))
        # Field IDs can change on full navigation; restore by unambiguous label/type/value.
        previous = self.history.get(self.page_number)
        if previous:
            restored, reused = {}, {}
            for field in after['fields']:
                matches = [f for f in previous['fields'] if (f['label'],f['type'],f['value']) == (field['label'],field['type'],field['value'])]
                if len(matches) == 1:
                    if matches[0].get('review'): restored[field['id']] = matches[0]['review']
                    if matches[0].get('saved_answer_id'): reused[field['id']] = matches[0]['saved_answer_id']
            self.field_reviews, self.reused_answers = restored, reused
        return self.snapshot()

    def refresh_root(self):
        pass

    def submit_button(self):
        buttons = []
        for frame in self.frames.values():
            matches = frame.get_by_role('button', name=re.compile(r'^Submit application$', re.I))
            buttons.extend(button for button in matches.all() if button.is_visible())
        return buttons[0] if len(buttons) == 1 else None

    def submit(self, approved_fingerprint):
        current = self.snapshot()
        if fingerprint(current) != approved_fingerprint or not current['can_submit']:
            raise ValueError('The live form changed or has unanswered fields. Review it again.')
        button = self.submit_button()
        if button is None:
            raise ValueError('The submission button is no longer available.')
        # This is the only application submission click in the codebase.
        button.click(timeout=15000)
        try:
            self.page.get_by_text(re.compile(r'(application has been (successfully )?submitted|thank you for applying|application received)', re.I)).first.wait_for(timeout=15000)
            return 'submitted'
        except Exception:
            return 'submission_unknown'
