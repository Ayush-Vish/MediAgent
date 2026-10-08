from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl, field_validator


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    request_id: UUID

    @field_validator('message')
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError('Message cannot be blank')
        return value.strip()


class Citation(BaseModel):
    id: str
    title: str
    url: str
    checked_at: str
    page: int | None = None
    excerpt: str


class SeverityInfo(BaseModel):
    level: Literal['emergency', 'severe', 'moderate', 'general'] = 'general'
    confidence: float = 0.0
    summary: str = ''


class Answer(BaseModel):
    text: str
    route: Literal['hospital', 'health', 'emergency', 'privacy', 'blocked', 'handoff', 'welcome'] = 'health'
    sources: list[Citation] = Field(default_factory=list)
    evidence: Literal['supported', 'limited', 'general', 'none'] = 'none'
    review_id: str | None = None
    mode: str = 'local'
    severity: SeverityInfo | None = None


class SourceInput(BaseModel):
    title: str = Field(min_length=3, max_length=180)
    url: HttpUrl
    text: str = Field(min_length=30, max_length=60000)
    category: Literal['hospital', 'health'] = 'hospital'
    checked_at: date
    page: int | None = Field(default=None, ge=1)
    conflict: bool = False

    @field_validator('url')
    @classmethod
    def https_only(cls, value):
        if value.scheme != 'https':
            raise ValueError('Sources must use HTTPS')
        return value

    @field_validator('checked_at')
    @classmethod
    def not_future(cls, value):
        if value > date.today():
            raise ValueError('Source review date cannot be in the future')
        return value


class SourceDecision(BaseModel):
    approved: bool


class ReviewReply(BaseModel):
    reply: str = Field(min_length=3, max_length=2000)


class DoctorMessage(BaseModel):
    conversation_id: str = Field(min_length=10, max_length=160)
    text: str = Field(min_length=1, max_length=2000)
    request_id: UUID


class TimelineSummaryRequest(BaseModel):
    since_days: int = Field(default=30, ge=1, le=90)


class EmbedRequest(BaseModel):
    texts: list[str] = Field(min_length=1, max_length=50)


class RerankRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    passages: list[str] = Field(min_length=1, max_length=50)
    top_k: int = Field(default=4, ge=1, le=20)


class RagSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    category: Literal['hospital', 'health'] = 'health'
    top_k: int = Field(default=4, ge=1, le=20)


class UserRegister(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    email: str = Field(min_length=5, max_length=150)
    password: str = Field(min_length=6, max_length=100)
    phone: str = Field(default='', max_length=30)
    age: int | None = Field(default=None, ge=0, le=130)
    gender: str = Field(default='', max_length=30)
    emergency_contact: str = Field(default='', max_length=100)
    emergency_phone: str = Field(default='', max_length=30)

    @field_validator('email')
    @classmethod
    def clean_email(cls, v: str) -> str:
        v = v.strip().lower()
        if '@' not in v or '.' not in v:
            raise ValueError('Invalid email address')
        return v


class UserLogin(BaseModel):
    email: str = Field(min_length=5, max_length=150)
    password: str = Field(min_length=1, max_length=100)

    @field_validator('email')
    @classmethod
    def clean_email(cls, v: str) -> str:
        return v.strip().lower()


class UserProfile(BaseModel):
    id: str
    email: str
    name: str
    phone: str = ''
    age: int | None = None
    gender: str = ''
    emergency_contact: str = ''
    emergency_phone: str = ''
    created_at: float | None = None


class AuthResponse(BaseModel):
    token: str
    user: UserProfile


class SymptomEvent(BaseModel):
    id: str
    user_id: str
    user_name: str
    user_email: str = ''
    timestamp: float
    query: str
    topic: str | None = None
    severity: Literal['emergency', 'severe', 'moderate', 'general'] = 'general'
    confidence: float = 0.0
    summary: str = ''
    route: str = 'health'
    answer_snippet: str = ''


class PatientSummaryResponse(BaseModel):
    patient: UserProfile | dict
    since_days: int
    total_events: int
    peak_severity: str
    severity_counts: dict[str, int]
    frequent_topics: list[str]
    clinical_progression: str
    timeline: list[dict]
