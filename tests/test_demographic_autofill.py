"""Explicit profile choices fill equivalent controls, never inferred identities."""
from src.applications.questions import apply, match, matching_options


class Form:
    def __init__(self,label,options,kind='combobox'):
        self.field={'id':'q','label':label,'type':kind,'supported':True,'value':'','options':options}
        self.field_reviews={}
        self.queries=[]
    def snapshot(self):return {'fields':[self.field]}
    def find_options(self,field_id,query):
        self.queries.append(query)
        return self.field['options']
    def edit(self,field_id,value):self.field['value']=value


def test_saved_demographics_map_choices_and_still_require_review():
    cases=[('gender','Man','Gender*','Male'),
           ('hispanic','Yes','Are you Hispanic/Latino?*','Yes'),
           ('veteran','I am not a protected veteran','Veteran Status*','I am not a protected veteran'),
           ('disability','Prefer not to disclose','Disability Status*','I do not want to answer')]
    for key,saved,label,choice in cases:
        form=Form(label,[{'label':choice,'value':'employer-choice'}])
        apply({'application_answers':{key:saved}},form)
        assert form.field['value']=='employer-choice'
        assert form.queries==['']
        assert form.field_reviews['q']['pending']
        assert form.field_reviews['q']['source'].startswith('Profile fact:')
        if choice!=saved:assert form.field_reviews['q']['status']=='new_wording'


def test_missing_conflicting_or_non_equivalent_choices_stay_blank():
    for answers in ({},{'gender':'Self-describe on each application'},{'gender':'Non-binary'}):
        form=Form('Gender',[{'label':'Male','value':'m'}]);apply({'application_answers':answers},form)
        assert form.field['value']==''
    form=Form('Gender',[{'label':'Male','value':'m'}]);form.field_reviews['q']={'conflict':True}
    apply({'application_answers':{'gender':'Man'}},form)
    assert form.field['value']==''
    assert match({'application_answers':{'gender':'Man'}},'Please share your gender pronouns.') is None
    assert matching_options([{'label':'I am not a veteran','value':'n'}],'I am not a protected veteran','veteran')[0]==[]
    ambiguous=[{'label':'Male','value':'1'},{'label':'Male','value':'2'}]
    form=Form('Gender',ambiguous);apply({'application_answers':{'gender':'Man'}},form)
    assert form.field['value']==''


def test_combined_race_requires_its_own_explicit_choice():
    form=Form('Race',[{'label':'Hispanic or Latino','value':'h'}])
    profile={'application_answers':{'hispanic':'Yes','race':'White'}}
    apply(profile,form);assert form.field['value']==''
    profile['application_answers']['race_ethnicity']='Hispanic or Latino'
    apply(profile,form);assert form.field['value']=='h'
    assert form.field_reviews['q']['pending']


def test_learned_choice_equivalent_to_profile_is_not_a_conflict(monkeypatch):
    from src.applications.questions import flag_conflicts
    from src.applications import answers
    saved={'question':'gender','field_type':'select','company':'','value':'Male'}
    monkeypatch.setattr(answers,'list_answers',lambda path:[saved])
    profile={'application_answers':{'gender':'Man'}}
    form=Form('Gender',[],kind='select')
    flag_conflicts(None,profile,form,'Acme')
    assert form.field_reviews=={}
    saved['value']='Female'
    flag_conflicts(None,profile,form,'Acme')
    assert form.field_reviews['q']['conflict']
