"""LLM-based input and output policy decisions.

The guardrail model returns structured data. Application code validates that
shape but does not classify user or assistant prose with keyword/regex rules.
"""
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.llm_pool import LLMUnavailable
from app.observability import event, span


class InputDecision(BaseModel):
    action: Literal[
        'allow', 'welcome', 'human_review', 'emergency', 'privacy',
        'blocked', 'handoff', 'out_of_scope',
    ]
    category: Literal['health', 'hospital'] = 'health'
    severity: Literal['general', 'moderate', 'severe', 'emergency'] = 'general'
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    summary: str = Field(default='', max_length=180)
    topic: str | None = Field(default=None, max_length=40)
    language: Literal['english', 'hinglish', 'hindi'] = 'english'


class OutputDecision(BaseModel):
    allowed: bool
    reason: str = Field(default='', max_length=180)
    replacement: str = Field(default='', max_length=4000)
    safe_source_ids: list[int] = Field(max_length=5)

    @model_validator(mode='after')
    def rejected_output_requires_replacement(self):
        if not self.allowed and not self.replacement.strip():
            raise ValueError('Rejected output requires a safe replacement')
        if any(type(index) is not int or index < 0 for index in self.safe_source_ids):
            raise ValueError('Source IDs must be non-negative integers')
        return self


class LLMGuardrails:
    def __init__(self, settings):
        self.settings = settings

    def _provider(self):
        from app.providers import configured_provider
        provider, config = configured_provider(self.settings)
        if provider.name == 'local' or config is None:
            raise LLMUnavailable()
        return provider

    def inspect_input(self, query: str, context=None, previous_topic=None) -> InputDecision:
        try:
            provider = self._provider()
            with span('guardrail.input', provider=provider.name):
                result = provider.guard_input(query, context or [], previous_topic)
                decision = InputDecision.model_validate(result)
        except LLMUnavailable:
            raise
        except Exception as error:
            event('guardrail.input.failed', error_type=type(error).__name__)
            raise LLMUnavailable() from None
        event('guardrail.input.decision', action=decision.action, category=decision.category,
              severity=decision.severity, topic=decision.topic)
        return decision

    def inspect_output(self, query: str, answer: str, category: str, sources=None,
                       severity='general') -> OutputDecision:
        try:
            provider = self._provider()
            with span('guardrail.output', provider=provider.name):
                result = provider.guard_output(query, answer, category, sources or [], severity)
                decision = OutputDecision.model_validate(result)
        except LLMUnavailable:
            raise
        except Exception as error:
            event('guardrail.output.failed', error_type=type(error).__name__)
            raise LLMUnavailable() from None
        event('guardrail.output.decision', allowed=decision.allowed, reason=decision.reason)
        return decision
