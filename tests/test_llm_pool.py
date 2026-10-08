import httpx
import pytest

from app.key_pool import PoolUnavailable
from app.llm_pool import LLMPool, LLMUnavailable
from app.providers import configured_provider
from tests.test_chat import ask


class FakeProvider:
    name = 'fake'

    def __init__(self, result):
        self.result = result
        self.calls = 0

    def generate(self, *args):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    generate_general = generate
    select = generate


def test_generate_falls_through_providers():
    first, second = FakeProvider(PoolUnavailable()), FakeProvider('answer')
    pool = LLMPool([first, second])
    assert pool.generate('question') == 'answer'
    assert pool.generate('question', 'health', []) == 'answer'
    assert first.calls == second.calls == 2


def test_all_failed_and_empty_pool_raise_safe_error():
    for providers in ([], [FakeProvider(None), FakeProvider(httpx.ConnectError('secret'))]):
        with pytest.raises(LLMUnavailable, match='No AI model') as error:
            LLMPool(providers).generate('question')
        assert 'secret' not in str(error.value)


def test_settings_build_all_providers(settings):
    settings.ai_provider = 'pool'
    settings.openrouter_api_keys, settings.openrouter_model = 'a,b', 'router-model'
    settings.gemini_api_keys, settings.gemini_model = 'c', 'gemini-model'
    settings.groq_api_keys, settings.groq_model = 'g', 'groq-model'
    settings.ai_api_keys, settings.ai_model = 'd', 'compatible-model'
    provider, _ = configured_provider(settings)
    assert [p.name for p in provider.providers] == ['openrouter', 'gemini', 'groq', 'openai_compatible']
    other, _ = configured_provider(settings)
    assert other.providers[0].pool is provider.providers[0].pool


def test_unavailable_reaches_frontend_and_is_not_saved(client, app):
    app.state.service.graph.settings.ai_provider = 'pool'
    result = ask(client, 'How can I book an appointment?')
    assert result.status_code == 503
    assert result.json()['code'] == 'LLM_UNAVAILABLE'
    assert 'No AI model' in result.json()['error']
    assert client.get('/api/history').json()['turns'] == []
    assert ask(client, 'hi').status_code == 200


def test_pool_daily_quota_reaches_frontend(client, app):
    settings = app.state.service.graph.settings
    settings.ai_provider, settings.ai_daily_limit = 'pool', 0
    assert ask(client, 'How can I book an appointment?').status_code == 503


def test_groq_endpoint_rotation_and_pool_fallback(settings, monkeypatch):
    settings.ai_provider = 'groq'
    settings.groq_api_keys, settings.groq_model = 'groq-one,groq-two', 'test-model'
    provider, _ = configured_provider(settings)
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        status = 429 if len(calls) == 1 else 200
        return httpx.Response(status, request=httpx.Request('POST', url),
                              headers={'Retry-After': '60'},
                              json={'choices': [{'message': {'content': 'Generated answer [S1]'}}]})

    monkeypatch.setattr(httpx, 'post', post)
    assert provider.generate('query', 'health', []) == 'Generated answer [S1]'
    assert calls[0][0] == 'https://api.groq.com/openai/v1/chat/completions'
    assert calls[1][1]['headers']['Authorization'] == 'Bearer groq-two'
    settings.ai_provider = 'pool'
    pooled, _ = configured_provider(settings)
    assert pooled.providers[0].pool is provider.pool
    assert LLMPool([FakeProvider(PoolUnavailable()), *pooled.providers]).generate('query')
