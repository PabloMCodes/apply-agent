"""Explicit local answer memory. Exact questions only, with company scope by default."""
import json
import re
from src.db.database import connect


def question(label):
    return re.sub(r'\s+', ' ', label.replace('*', '')).strip().casefold()


def remember(path, field, company, scope):
    if not field['supported'] or field['type'] in ('checkbox', 'radio', 'password', 'file', 'custom'):
        raise ValueError('This control cannot be remembered.')
    value = field['value']
    if field['type'] == 'select':
        value = next(o['label'] for o in field['options'] if o['value'] == value)
    with connect(path) as conn:
        conn.execute("""INSERT INTO saved_answers(question,field_type,company,value) VALUES (?,?,?,?)
            ON CONFLICT(question,field_type,company) DO UPDATE SET value=excluded.value""",
            (question(field['label']), field['type'], company.casefold() if scope == 'company' else '', json.dumps(value)))


def list_answers(path):
    with connect(path) as conn:
        return [dict(row) | {'value': json.loads(row['value'])} for row in conn.execute('SELECT * FROM saved_answers ORDER BY id DESC')]


def forget(path, answer_id):
    with connect(path) as conn:
        conn.execute('DELETE FROM saved_answers WHERE id=?', (answer_id,))


def apply(path, session, company):
    saved = list_answers(path)
    fields = session.snapshot()['fields']
    reused = {}
    for field in fields:
        key = question(field['label'])
        if (isinstance(getattr(session, 'field_reviews', None), dict) and session.field_reviews.get(field['id'], {}).get('conflict')) or field['value'] or not field['supported'] or sum(question(f['label']) == key for f in fields) != 1:
            continue
        matches = [a for a in saved if a['question'] == key and a['field_type'] == field['type'] and a['company'] in ('', company.casefold())]
        matches.sort(key=lambda a: bool(a['company']), reverse=True)
        if not matches:
            continue
        answer = matches[0]
        try:
            value = answer['value']
            if field['type'] == 'combobox':
                session.find_options(field['id'], str(value))
            elif field['type'] == 'select':
                choices = [o for o in field['options'] if o['label'] == value]
                if len(choices) != 1:
                    continue
                value = choices[0]['value']
            session.edit(field['id'], value)
            reused[field['id']] = answer['id']
        except Exception:
            # Changed employer choices should leave a question for manual review.
            continue
    return reused
