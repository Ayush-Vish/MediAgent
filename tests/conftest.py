import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.guardrails import InputDecision, OutputDecision
from app.knowledge import topic_for
from app.main import create_app


class FakeGuardrails:
    """Deterministic test double; production uses LLMGuardrails."""

    def inspect_input(self, query, context=None, previous_topic=None):
        text = query.lower()
        topic = topic_for(query) or previous_topic
        hospital_topics = {'appointment', 'preparation', 'contact', 'fees', 'insurance',
                           'campus', 'changes', 'emeds', 'international'}
        category = 'hospital' if topic in hospital_topics or any(
            word in text for word in ('hospital', 'reception', 'visiting hours', 'mri cost', 'parking')
        ) else 'health'
        action, severity, confidence, summary = 'allow', 'moderate', 0.3, 'General health query'
        if text.strip() in {'hi', 'hello', 'hey', '/start', 'help'}:
            action, severity, confidence, summary = 'welcome', 'general', 0.0, 'Greeting'
        elif 'human' in text or 'staff' in text:
            action, severity, confidence, summary = 'human_review', 'general', 0.0, 'Staff review requested'
        elif any(term in text for term in ('chest pain', 'cannot breathe', "can't breathe", 'kill myself')):
            action, severity, confidence, summary = 'emergency', 'emergency', 0.95, 'Possible emergency'
        elif 'high fever' in text and ('severe headache' in text or '5 days' in text):
            severity, confidence, summary = 'severe', 0.85, 'Persistent fever and severe headache'
        elif '@' in text or 'my name is' in text:
            action, severity, confidence, summary = 'privacy', 'general', 0.9, 'Personal information supplied'
        elif 'ignore previous instructions' in text or 'reveal secrets' in text:
            action, severity, confidence, summary = 'blocked', 'general', 0.95, 'Prompt injection attempt'
        elif 'should i take' in text or 'diagnose me' in text:
            action, severity, confidence, summary = 'handoff', 'moderate', 0.9, 'Personal treatment request'
        elif 'poem about the moon' in text:
            action, category, severity, confidence = 'out_of_scope', 'health', 'general', 0.95
            summary = 'Unrelated request'
        return InputDecision(action=action, category=category, severity=severity,
                             confidence=confidence, summary=summary, topic=topic,
                             language='english')

    def inspect_output(self, query, answer, category, sources=None, severity='general'):
        if severity == 'severe':
            return OutputDecision(
                allowed=False,
                reason='missing urgency guidance',
                replacement=(
                    '⚠️ **Important Health Notice**: Please seek prompt medical attention.\n\n'
                    + answer
                ),
                safe_source_ids=list(range(len(sources or []))),
            )
        return OutputDecision(allowed=True, reason='safe', replacement='',
                              safe_source_ids=list(range(len(sources or []))))


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, environment='local', database_url=f'sqlite:///{tmp_path / "test.db"}',
                    admin_token='test-staff-token', source_max_age_days=10000, web_search_enabled=False)


@pytest.fixture
def app(settings):
    application = create_app(settings)
    application.state.service.graph.guardrails = FakeGuardrails()
    return application


@pytest.fixture
def client(app):
    with TestClient(app) as client:
        client.post('/api/session')
        yield client


@pytest.fixture
def staff():
    return {'Authorization': 'Bearer test-staff-token'}
