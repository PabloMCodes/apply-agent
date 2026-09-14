from src.setup.resume_profile import suggest
from src.setup.application_fields import facts, race_suggestion
from src.applications.questions import match
from src.api.schemas import Profile
import pytest

TEXT='''Alex Example
alex@example.com | (212) 555-0199 | New York, NY
linkedin.com/in/alex-example | github.com/alexdev
Portfolio: https://alex.dev
Education
Example University
Bachelor of Science in Computer Science
GPA: 3.8/4.0
Expected graduation May 2027
Skills
Python, JavaScript, React, SQL, C++
Experience
Current company: Example Inc.
Current title: Software Engineer
'''


def test_local_extraction_and_no_sensitive_inference():
    result=suggest(TEXT+'\nUS citizen. Veteran. Disability: No. Gender: Man.')
    values=result['values']
    assert values['first_name']=='Alex' and values['last_name']=='Example'
    assert values['email']=='alex@example.com'
    assert values['github']=='https://github.com/alexdev'
    assert values['website']=='https://alex.dev'
    assert values['location']=='New York, NY'
    assert {'Python','JavaScript','React','SQL','C++'} <= set(values['skills'])
    answers=values['application_answers']
    assert answers['school']=='Example University'
    assert answers['discipline']=='Computer Science'
    assert answers['graduation_year']=='2027'
    assert answers['gpa']=='3.8/4.0'
    assert not set(answers)&{'gender','citizenship','veteran','disability','work_authorized'}
    assert result['evidence']['school']=='Example University'


def test_empty_and_sparse_resumes_do_not_invent_profile():
    assert suggest('')['values']=={'application_answers':{}}
    result=suggest('Software Engineer\nSkills\nPython\nExperience\nExample University project')
    assert 'first_name' not in result['values']
    assert 'school' not in result['values']['application_answers']


def test_optional_answers_and_country_specific_matching():
    profile=Profile(application_answers={'gender':'Prefer not to disclose','work_authorized':'Yes','authorization_country':'United States'}).model_dump()
    assert match(profile,'Gender')['value']=='Prefer not to disclose'
    assert match(profile,'Gender')['suggest_only']
    assert match(profile,'Are you legally authorized to work in the United States?')['value']=='Yes'
    assert match(profile,'Are you legally authorized to work in Canada?') is None
    assert facts(Profile().model_dump())==[]
    with pytest.raises(ValueError): Profile(application_answers={'gender':'auto infer'})


def test_combined_race_does_not_infer_from_ethnicity():
    field={'label':'Race','options':[{'label':'Hispanic or Latino','value':'h'}]}
    profile={'application_answers':{'hispanic':'Yes','race':'White'}}
    assert race_suggestion(profile,field) is None
    profile['application_answers']['race_ethnicity']='Hispanic or Latino'
    assert race_suggestion(profile,field)['value']=='Hispanic or Latino'
    assert race_suggestion(profile,dict(field,options=[])) is None


def test_resume_upload_returns_suggestions_without_overwriting_saved_profile(tmp_path):
    from fastapi.testclient import TestClient
    from src.api.app import create_app
    with TestClient(create_app(tmp_path/'jobs.sqlite3')) as client:
        client.put('/profile',json={'first_name':'Existing','application_answers':{'gender':'Prefer not to disclose'}})
        upload=client.post('/resumes',data={'title':'SWE'},files={'file':('resume.txt',TEXT.encode(),'text/plain')})
        assert upload.status_code==201
        assert upload.json()['profile_suggestions']['values']['first_name']=='Alex'
        assert client.get('/profile').json()['first_name']=='Existing'
        assert client.get('/profile').json()['application_answers']['gender']=='Prefer not to disclose'
        assert client.get(f"/resumes/{upload.json()['id']}/profile-suggestions").json()==upload.json()['profile_suggestions']
        assert len(client.get('/profile/fields').json())>15
