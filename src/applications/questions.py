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
        # Only the structured voluntary answers opt into deterministic autofill.
        candidates.append((fact, exact))
    if len(candidates) != 1:
        return None
    fact, exact = candidates[0]
    return {'value':fact['answer'], 'status':'previously_confirmed' if exact else 'new_wording',
            'source':f"Profile fact: {fact['question']}" + (f" ({fact['context']})" if fact.get('context') else ''),
            'pending':True, 'suggest_only':(fact.get('sensitive',False) or sensitive(label)) and not fact.get('autofill',False),
            'answer_key':fact.get('id','').removeprefix('application:') if fact.get('autofill') else ''}


def apply(profile, session):
    fields = session.snapshot()['fields']
    for field in fields:
        if field['value'] or not field['supported'] or field['id'] in session.field_reviews:
            continue
        if field['type'] in ('checkbox','radio','file','custom','password'):
            continue
        if sum(question(f['label']) == question(field['label']) for f in fields) != 1:
            continue
        # Read custom choices before matching: they may distinguish separate race
        # from a combined race/ethnicity question. Never search using an assumed answer.
        if field['type']=='combobox' and (match(profile,field['label']) or question(field['label']) in ('race','race / ethnicity','race/ethnicity','race & ethnicity','race and ethnicity')):
            try:
                field['options']=session.find_options(field['id'], '')
            except Exception:
                continue
        result = race_suggestion(profile, field) or match(profile, field['label'])
        if not result:
            continue
        value = result['value']
        if result.pop('suggest_only'):
            session.field_reviews[field['id']] = dict(result, draft=value)
            continue
        try:
            if field['type'] in ('select','combobox'):
                options, mapped = matching_options(field['options'], value, result.get('answer_key',''))
                if len(options) != 1:
                    session.field_reviews[field['id']] = dict(result, draft=value,
                        status='needs_answer', source=result['source']+' — no unique equivalent employer choice')
                    continue
                value = options[0]['value']
                if mapped:
                    result['status']='new_wording'
                    result['source'] += f" — saved choice: {result['value']}; employer choice: {options[0]['label']}"
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
        known=match(profile,field['label'])
        normalized=set()
        for value in values:
            equivalent=known and matching_options([{'label':str(value)}],known['value'],known.get('answer_key',''))[0]
            normalized.add(question(known['value'] if equivalent else value))
        if len(normalized) > 1:
            session.field_reviews[field['id']] = {'status':'needs_answer','pending':True,'conflict':True,
                'source':'Conflicting profile facts or saved answers. Correct the sources or answer this question yourself.'}


# Narrow, field-specific wording equivalents. Do not infer one identity from another.
OPTION_EQUIVALENTS = {
    'gender': [('Man','Male'), ('Woman','Female')],
    'veteran': [('I identify as a protected veteran','I identify as one or more of the classifications of a protected veteran')],
    'disability': [
        ('Yes, I have a disability, or have had one in the past','Yes, I have a disability, or have had one in the past.'),
        ('No, I do not have a disability and have not had one in the past','No, I do not have a disability and have not had one in the past.'),
    ],
}
DECLINE_EQUIVALENTS = ('Prefer not to disclose','I do not wish to answer','I don’t wish to answer','I do not want to answer','Decline to self-identify','Decline to answer','I choose not to disclose')


def matching_options(options, value, answer_key):
    exact=[o for o in options if question(o['label'])==question(value)]
    if exact:return exact, False
    groups=list(OPTION_EQUIVALENTS.get(answer_key,[]))
    if answer_key in ('gender','hispanic','veteran','disability','race','race_ethnicity'):
        groups.append(DECLINE_EQUIVALENTS)
    aliases=set()
    for group in groups:
        normalized={question(v) for v in group}
        if question(value) in normalized:aliases.update(normalized)
    return [o for o in options if question(o['label']) in aliases], True
