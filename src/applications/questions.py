"""Match explicit facts and user-written variants; never infer demographic categories."""
import re
from src.applications.answers import question
from src.setup.application_fields import facts as application_facts, race_suggestion

SENSITIVE = re.compile(r'hispanic|latino|ethnic|\brace\b|racial|gender|disabilit|veteran|religio|sexual|citizenship|authorized|authorised|sponsorship|work permit', re.I)


def sensitive(label):
    return bool(SENSITIVE.search(label))


def match(profile, label):
    key = question(label)
    candidates = []
    for fact in (profile.get('facts', []) + application_facts(profile)):
        exact = key == question(fact['question'])
        variant = key in [question(v) for v in fact.get('variants', [])]
        context = fact.get('context', '')
        if not (exact or variant) or (context and question(context) not in key):
            continue
        # Sensitive variants are suggestions, never entered automatically.
        candidates.append((fact, exact))
    if len(candidates) != 1:
        return None
    fact, exact = candidates[0]
    return {'value':fact['answer'], 'status':'previously_confirmed' if exact else 'new_wording',
            'source':f"Profile fact: {fact['question']}" + (f" ({fact['context']})" if fact.get('context') else ''),
            'pending':True, 'suggest_only':fact.get('sensitive',False) or sensitive(label)}


def apply(profile, session):
    fields = session.snapshot()['fields']
    for field in fields:
        if field['value'] or not field['supported'] or field['id'] in session.field_reviews:
            continue
        if field['type'] in ('checkbox','radio','file','custom','password'):
            continue
        if sum(question(f['label']) == question(field['label']) for f in fields) != 1:
            continue
        result = race_suggestion(profile, field) or match(profile, field['label'])
        if not result:
            continue
        value = result['value']
        if result.pop('suggest_only'):
            session.field_reviews[field['id']] = dict(result, draft=value)
            continue
        try:
            if field['type'] == 'select':
                options = [o for o in field['options'] if question(o['label']) == question(value)]
                if len(options) != 1:
                    continue
                value = options[0]['value']
            if field['type'] == 'combobox':
                session.find_options(field['id'], value)
            session.edit(field['id'], value)
            session.field_reviews[field['id']] = result
        except Exception:
            continue


def flag_conflicts(path, profile, session, company):
    """Do not let an old saved answer silently override a changed profile fact."""
    from src.applications.answers import list_answers
    saved = list_answers(path)
    for field in session.snapshot()['fields']:
        if field['value']:
            continue
        key = question(field['label'])
        values = [f['answer'] for f in (profile.get('facts',[]) + application_facts(profile)) if
                  key in [question(f['question']),*[question(v) for v in f.get('variants',[])]] and
                  (not f.get('context') or question(f['context']) in key)]
        matches = [a for a in saved if a['question']==key and a['field_type']==field['type'] and a['company'] in ('',company.casefold())]
        matches.sort(key=lambda a:bool(a['company']),reverse=True)
        if matches:
            values.append(str(matches[0]['value']))
        if len({question(v) for v in values}) > 1:
            session.field_reviews[field['id']] = {'status':'needs_answer','pending':True,'conflict':True,
                'source':'Conflicting profile facts or saved answers. Correct the sources or answer this question yourself.'}
