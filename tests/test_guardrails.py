import pytest

from app.guardrails import LLMGuardrails
from app.llm_pool import LLMUnavailable


class StructuredProvider:
    name = 'test-llm'

    def guard_input(self, query, context, previous_topic):
        return {
            'action': 'allow', 'category': 'health', 'severity': 'moderate',
            'confidence': 0.62, 'summary': 'General medical education',
            'topic': 'medicine', 'language': 'english',
        }

    def guard_output(self, query, answer, category, sources=None, severity='general'):
        return {'allowed': True, 'reason': 'safe general education', 'replacement': '',
                'safe_source_ids': []}


def test_llm_guardrails_validate_structured_provider_decisions(settings, monkeypatch):
    provider = StructuredProvider()
    monkeypatch.setattr('app.providers.configured_provider', lambda value: (provider, object()))
    guardrails = LLMGuardrails(settings)

    input_decision = guardrails.inspect_input('What is paracetamol?', [], None)
    output_decision = guardrails.inspect_output(
        'What is paracetamol?', 'General educational information.', 'health')

    assert input_decision.topic == 'medicine'
    assert input_decision.confidence == 0.62
    assert output_decision.allowed is True


def test_llm_guardrails_fail_closed_on_malformed_decision(settings, monkeypatch):
    provider = StructuredProvider()
    provider.guard_output = lambda query, answer, category, sources=None, severity='general': {
        'allowed': False
    }
    monkeypatch.setattr('app.providers.configured_provider', lambda value: (provider, object()))

    with pytest.raises(LLMUnavailable):
        LLMGuardrails(settings).inspect_output('question', 'answer', 'health')
