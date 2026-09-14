"""Common US SWE application fields, researched September 2026. All optional."""
YES_NO = ['Yes', 'No']
DECLINE = 'Prefer not to disclose'


def item(key, label, section, options=None, variants=(), sensitive=False, hint=''):
    return dict(key=key,label=label,section=section,options=options,variants=list(variants),sensitive=sensitive,hint=hint)


FIELDS = [
    item('school','School / university','Education',variants=['School','University']),
    item('degree','Degree','Education',variants=['Highest degree']),
    item('discipline','Major / field of study','Education',variants=['Discipline','Major','Field of study']),
    item('graduation_year','Graduation year','Education',variants=['End date year','Expected graduation year']),
    item('gpa','GPA (optional)','Education',variants=['GPA']),
    item('current_company','Current company','Employment',variants=['Current employer']),
    item('current_title','Current job title','Employment',variants=['Current title']),
    item('years_experience','Years of professional software engineering experience','Employment'),
    item('interview_language','Preferred programming language for interviews','Employment'),
    item('start_availability','When can you start?','Employment',variants=['How soon would you be available to start?','Earliest start date']),
    item('salary_expectations','Desired base salary (include currency)','Employment',variants=['What are your desired base salary expectations?']),
    item('relocate','Are you willing to relocate?','Employment',YES_NO+['Depends on location']),
    item('hybrid','Are you comfortable working in a hybrid position?','Employment',YES_NO),
    item('authorization_country','Country these eligibility answers apply to','Work eligibility',hint='For example: United States. Eligibility is never inferred from your address.'),
    item('citizenship','Citizenship country/countries','Work eligibility',sensitive=True,hint='Optional. Citizenship is separate from permission to work.'),
    item('citizenship_status','U.S. citizenship / immigration status','Work eligibility',['U.S. citizen','U.S. national','Lawful permanent resident','Temporary visa / immigration status','Other',DECLINE],sensitive=True,hint='Optional. This answer does not determine work authorization or sponsorship for you.'),
    item('work_authorized','Are you legally authorized to work in this country?','Work eligibility',YES_NO,sensitive=True),
    item('sponsorship','Will you need employment visa sponsorship now or in the future?','Work eligibility',YES_NO,sensitive=True),
    item('gender','Gender','Voluntary self-identification',['Woman','Man','Non-binary','Self-describe on each application',DECLINE],variants=['What gender do you identify as?'],sensitive=True),
    item('hispanic','Are you Hispanic/Latino?','Voluntary self-identification',YES_NO+[DECLINE],sensitive=True),
    item('race','Race (when asked separately)','Voluntary self-identification',['American Indian or Alaska Native','Asian','Black or African American','Native Hawaiian or Other Pacific Islander','White','Two or more races','Self-describe on each application',DECLINE],sensitive=True),
    item('race_ethnicity','Race / ethnicity (combined question)','Voluntary self-identification',['Hispanic or Latino','American Indian or Alaska Native (Not Hispanic or Latino)','Asian (Not Hispanic or Latino)','Black or African American (Not Hispanic or Latino)','Native Hawaiian or Other Pacific Islander (Not Hispanic or Latino)','White (Not Hispanic or Latino)','Two or More Races (Not Hispanic or Latino)',DECLINE],sensitive=True,hint='Choose explicitly for combined forms; we do not derive this from separate race or ethnicity answers.'),
    item('veteran','Protected veteran status','Voluntary self-identification',['I identify as a protected veteran','I am not a protected veteran',DECLINE],variants=['Veteran Status'],sensitive=True),
    item('disability','Disability status','Voluntary self-identification',['Yes, I have a disability, or have had one in the past','No, I do not have a disability and have not had one in the past',DECLINE],sensitive=True),
]


def facts(profile):
    """Convert saved application answers to reviewable worker suggestions."""
    values=profile.get('application_answers',{})
    result=[]
    for field in FIELDS:
        key=field['key']; value=values.get(key,'')
        if not value or key in ('authorization_country','race','race_ethnicity') or value=='Self-describe on each application':
            continue
        labels=[field['label'],*field['variants']]
        if key in ('work_authorized','sponsorship'):
            # Country-less questions cannot borrow eligibility from another jurisdiction.
            country=values.get('authorization_country','').strip()
            countries=['United States','US','U.S.'] if country.casefold() in ('united states','us','usa','u.s.') else [country]
            if not country: continue
            labels=[]
            for c in countries:
                labels += ([f'Are you legally authorized to work in the {c}?',f'Are you legally authorized to work in {c}?',f'Are you legally authorized to work for any employers in the {c}?'] if key=='work_authorized' else
                           [f'Will you need sponsorship to work in the {c} now or anytime in the future?',f'Will you require sponsorship to work in {c}?'])
        result.append({'id':'application:'+key,'question':labels[0],'variants':labels[1:],'answer':value,'sensitive':field['sensitive'],'context':'','autofill':key in ('gender','hispanic','veteran','disability')})
    return result


def race_suggestion(profile, field):
    """Use employer choices to distinguish combined forms; never cross-map categories."""
    labels=[str(o['label']).casefold() for o in field.get('options',[])]
    label=field['label'].casefold().replace('*','').strip()
    if label not in ('race','race / ethnicity','race/ethnicity','race & ethnicity','race and ethnicity','race (when asked separately)'):
        return None
    combined=any('hispanic' in option or 'latino' in option for option in labels) or 'ethnic' in label
    if not labels: return None
    value=profile.get('application_answers',{}).get('race_ethnicity' if combined else 'race','')
    if not value or value=='Self-describe on each application': return None
    return {'value':value,'draft':value,'status':'new_wording','pending':True,'source':'Profile: '+('combined race / ethnicity' if combined else 'separate race'),'suggest_only':False,'answer_key':'race_ethnicity' if combined else 'race'}
