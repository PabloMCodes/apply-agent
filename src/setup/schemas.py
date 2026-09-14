from typing import Annotated, Literal
from pydantic import Field, HttpUrl, field_validator
from src.api.schemas import InputModel, JobInput

Keyword = Annotated[str, Field(min_length=1, max_length=100)]


class Preferences(InputModel):
    review_slots: int = Field(default=5, ge=1, le=20)
    roles: list[Keyword] = Field(default_factory=list, max_length=100)
    locations: list[Keyword] = Field(default_factory=list, max_length=100)
    exclude_keywords: list[Keyword] = Field(default_factory=list, max_length=100)
    poll_minutes: int = Field(default=15, ge=1, le=1440)
    monitoring_enabled: bool = False
    review_base_url: str = Field(default='', max_length=2000)

    @field_validator('review_base_url')
    @classmethod
    def valid_review_url(cls, value):
        if value:
            return str(HttpUrl(value)).rstrip('/')
        return value


class SourceInput(InputModel):
    name: str = Field(min_length=1, max_length=300)
    url: HttpUrl


class SourceUpdate(InputModel):
    enabled: bool


class TelegramConnect(InputModel):
    token: str = Field(min_length=10, max_length=200, repr=False)


class EditField(InputModel):
    remember: bool = False
    scope: Literal["company", "all"] = "company"
    revision: int = Field(ge=1)
    field_id: str = Field(min_length=1, max_length=100)
    value: str | bool = Field(union_mode='left_to_right')


class ReviewAction(InputModel):
    revision: int = Field(ge=1)


class FieldOptions(ReviewAction):
    field_id: str = Field(min_length=1, max_length=100)
    query: str = Field(default='', max_length=200)


class PrepareBatch(InputModel):
    job_ids: list[int] = Field(min_length=1, max_length=500)

    resume_id: int | None = Field(default=None, ge=1)


class BulkJobs(InputModel):
    jobs: list[JobInput] = Field(min_length=1, max_length=500)


class ResumeUpdate(InputModel):
    title: str = Field(min_length=1, max_length=200)
    roles: str = Field(default='', max_length=2000)


class Navigate(ReviewAction):
    direction: Literal['next', 'back']


class SuggestAnswer(ReviewAction):
    field_id: str = Field(min_length=1, max_length=100)


class AISettings(InputModel):
    browser_assistance: bool = False
    enabled: bool = False
    base_url: str = Field(default='', max_length=2000)
    model: str = Field(default='', max_length=200)
    api_key: str | None = Field(default=None, max_length=1000, repr=False)

    @field_validator('base_url')
    @classmethod
    def validate_endpoint(cls, value):
        from urllib.parse import urlparse
        if value:
            parsed = urlparse(value)
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError('Use an API base URL without credentials, query, or fragment.')
            if parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in ('localhost','127.0.0.1','::1')):
                raise ValueError('Use HTTPS, or HTTP for a local model on localhost.')
        return value.rstrip('/')


class BrowserControl(InputModel):
    operation: Literal['start','refresh','click','type','insert','scroll','key','tab','save_session','resume','ai','mark_final','upload_resume']
    token: str = Field(default='',max_length=100)
    x: float = Field(default=0,ge=0,le=1100)
    y: float = Field(default=0,ge=0,le=850)
    text: str = Field(default='',max_length=20000,repr=False)
    delta: int = Field(default=0,ge=-2000,le=2000)
    key: Literal['Tab','Escape','Backspace','ArrowUp','ArrowDown','ArrowLeft','ArrowRight','Home','End','Delete','Shift+Tab','ControlOrMeta+A'] = 'Tab'
    tab: int = Field(default=0,ge=0,le=20)


class ForgetSession(InputModel):
    origin: HttpUrl
