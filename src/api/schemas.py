"""Request validation and response contracts for agents and API docs."""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

JobStatus = Literal['new', 'saved', 'skipped', 'ready_for_review', 'applied']


class InputModel(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class JobInput(InputModel):
    company: str = Field(min_length=1, max_length=300)
    title: str = Field(min_length=1, max_length=500)
    location: str = Field(min_length=1, max_length=500)
    application_url: HttpUrl
    source: str = Field(default='manual', min_length=1, max_length=2000)


class JobOutput(JobInput):
    id: int
    status: JobStatus
    notes: str
    created_at: str
    updated_at: str
    applied_at: str | None = None


class JobPage(BaseModel):
    items: list[JobOutput]
    total: int
    limit: int
    offset: int


class TrackingUpdate(InputModel):
    status: JobStatus
    notes: str = Field(default='', max_length=10000)


class IngestionInput(InputModel):
    source: Literal['intern_usa', 'new_grad_usa', 'intern_intl', 'new_grad_intl'] = 'intern_usa'


class IngestionOutput(BaseModel):
    id: int
    source_url: str
    found: int
    inserted: int
    updated: int
    created_at: str


class ProfileFact(InputModel):
    id: str = Field(min_length=1, max_length=100)
    question: str = Field(min_length=1, max_length=600)
    answer: str = Field(min_length=1, max_length=20000)
    variants: list[str] = Field(default_factory=list, max_length=100)
    context: str = Field(default='', max_length=300)
    sensitive: bool = False
    share_with_ai: bool = False


class Profile(InputModel):
    github: str = Field(default='', max_length=1000)
    application_answers: dict[str, str] = Field(default_factory=dict)

    @field_validator('application_answers')
    @classmethod
    def validate_application_answers(cls, values):
        from src.setup.application_fields import FIELDS
        fields = {f['key']: f for f in FIELDS}
        for key, value in values.items():
            if key not in fields or len(value) > 2000:
                raise ValueError('Unknown or oversized application answer.')
            if value and fields[key]['options'] and value not in fields[key]['options']:
                raise ValueError(f'Choose an available option for {key}.')
        return values

    experience: str = Field(default='', max_length=100000)
    accomplishments: str = Field(default='', max_length=100000)
    education: str = Field(default='', max_length=50000)
    work_preferences: str = Field(default='', max_length=20000)
    availability: str = Field(default='', max_length=10000)
    facts: list[ProfileFact] = Field(default_factory=list, max_length=1000)

    name: str = Field(default='', max_length=300)
    first_name: str = Field(default='', max_length=150)
    last_name: str = Field(default='', max_length=150)
    email: str = Field(default='', max_length=300)
    phone: str = Field(default='', max_length=100)
    location: str = Field(default='', max_length=300)
    linkedin: str = Field(default='', max_length=1000)
    website: str = Field(default='', max_length=1000)
    resume_text: str = Field(default='', max_length=100000)
    skills: list[str] = Field(default_factory=list, max_length=200)
    preferred_roles: list[str] = Field(default_factory=list, max_length=100)
    preferred_locations: list[str] = Field(default_factory=list, max_length=100)


class MatchRequest(InputModel):
    job_ids: list[int] = Field(min_length=1, max_length=100)


class MatchResult(BaseModel):
    job_id: int
    score: float = Field(ge=0, le=1)
    reasons: list[str]
