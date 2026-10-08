"""One generation interface over independently rate-limited provider objects."""
import logging
from app.observability import event, span

import httpx

from app.key_pool import PoolUnavailable, parse_keys

log = logging.getLogger(__name__)


class LLMUnavailable(RuntimeError):
    def __init__(self):
        super().__init__('No AI model is currently available. Please try again later.')


class LLMPool:
    name = 'pool'

    def __init__(self, providers):
        self.providers = tuple(providers)

    def _call(self, method, *args):
        for provider in self.providers:
            try:
                with span('llm.provider', provider=provider.name, operation=method):
                    result = getattr(provider, method)(*args)
                if result:
                    event('llm.provider.selected', provider=provider.name, operation=method)
                    return result
                event('llm.provider.fallback', provider=provider.name, reason='empty_result')
            except (PoolUnavailable, httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
                event('llm.provider.fallback', provider=provider.name, reason='unavailable')
                # No keys, request bodies, or upstream error text in logs or UI.
                log.info('LLM provider %s unavailable; trying next provider', provider.name)
        event('llm.exhausted', provider_count=len(self.providers))
        raise LLMUnavailable()

    def generate(self, query, category='health', sources=None, context=None):
        if sources is None:
            return self.generate_general(query, context)
        return self._call('generate', query, category, sources, context)

    def generate_general(self, query, context=None):
        return self._call('generate_general', query, context)

    def select(self, topic, sources):
        return self._call('select', topic, sources)

    def summarize(self, timeline):
        return self._call('summarize', timeline)

    @classmethod
    def from_settings(cls, settings):
        from app.providers import GeminiProvider, OpenAICompatibleProvider, ProviderConfig
        from app.key_pool import PoolPolicy
        policy = PoolPolicy(settings.ai_pool_rpm, settings.ai_pool_concurrency,
                            settings.ai_pool_max_attempts, settings.ai_pool_shared_limits)
        providers = []
        entries = [
            ('openrouter', settings.openrouter_api_keys or settings.openrouter_api_key,
             settings.openrouter_model, 'https://openrouter.ai/api/v1'),
            ('gemini', settings.gemini_api_keys or settings.gemini_api_key,
             settings.gemini_model, None),
            ('groq', settings.groq_api_keys or settings.groq_api_key,
             settings.groq_model, 'https://api.groq.com/openai/v1'),
            ('openai_compatible', settings.ai_api_keys or settings.ai_api_key,
             settings.ai_model, settings.ai_base_url),
        ]
        for name, keys, model, endpoint in entries:
            if not parse_keys(keys) or not model:
                continue
            config = ProviderConfig(keys, model, settings.ai_daily_limit, policy)
            providers.append(GeminiProvider(config) if name == 'gemini' else
                             OpenAICompatibleProvider(config, endpoint, name))
        return cls(providers)
