import json

import httpx
import pytest

from app.providers import GeminiProvider, LocalProvider, OpenAICompatibleProvider, ProviderConfig, parse_ids


def test_parse_ids_rejects_malformed_or_out_of_range_values():
    assert parse_ids([0, 1, 0], 2) == [0, 1]
    assert parse_ids([], 2) is None
    assert parse_ids([2], 2) is None
    assert parse_ids({'ids': [0]}, 2) is None
    assert parse_ids([True], 2) is None


def test_local_strategy_returns_all_approved_candidates():
    assert LocalProvider().select('appointment', [{'id': 'a'}, {'id': 'b'}]) == [0, 1]


def test_gemini_strategy_uses_fixed_public_evidence(monkeypatch):
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(200, request=httpx.Request('POST', url), json={
            'candidates': [{'content': {'parts': [{'text': '[1, 0]'}]}}]})

    monkeypatch.setattr(httpx, 'post', post)
    result = GeminiProvider(ProviderConfig('secret', 'gemini-test', 10)).select('appointment', [
        {'title': 'Public source', 'text': 'Book online.', 'url': 'https://hospital.example/book', 'checked_at': '2026-10-07'},
        {'title': 'Other source', 'text': 'Bring ID.', 'url': 'https://hospital.example/id', 'checked_at': '2026-10-07'},
    ])
    assert result == [1, 0]
    assert calls[0][0].startswith('https://generativelanguage.googleapis.com/')
    assert 'secret' not in json.dumps(calls[0][1]['json'])
    assert 'appointment' in json.dumps(calls[0][1]['json'])


def test_openrouter_strategy_supports_openai_compatible_shape(monkeypatch):
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(200, request=httpx.Request('POST', url), json={
            'choices': [{'message': {'content': '{"ids":[0]}'}}]})

    monkeypatch.setattr(httpx, 'post', post)
    result = OpenAICompatibleProvider(ProviderConfig('secret', 'openrouter/test', 10),
                                      'https://openrouter.ai/api/v1').select('appointment', [
        {'title': 'Public source', 'text': 'Book online.', 'url': 'https://hospital.example/book', 'checked_at': '2026-10-07'},
    ])
    assert result == [0]
    assert calls[0][0] == 'https://openrouter.ai/api/v1/chat/completions'
    assert calls[0][1]['headers']['Authorization'] == 'Bearer secret'


@pytest.mark.parametrize('kind', ['gemini', 'compatible'])
def test_provider_guardrails_request_and_parse_structured_json(kind, monkeypatch):
    provider = (GeminiProvider(ProviderConfig('secret', 'guard-model', 10)) if kind == 'gemini'
                else OpenAICompatibleProvider(ProviderConfig('secret', 'guard-model', 10),
                                              'https://example.com/v1', 'groq'))
    calls = []

    def post(url, **kwargs):
        calls.append(kwargs['json'])
        content = json.dumps({
            'action': 'allow', 'category': 'health', 'severity': 'moderate',
            'confidence': 0.5, 'summary': 'General education', 'topic': 'medicine',
            'language': 'english',
        }) if len(calls) == 1 else json.dumps({
            'allowed': True, 'reason': 'safe', 'replacement': '', 'safe_source_ids': [],
        })
        payload = ({'candidates': [{'content': {'parts': [{'text': content}]}}]}
                   if kind == 'gemini' else {'choices': [{'message': {'content': content}}]})
        return httpx.Response(200, request=httpx.Request('POST', url), json=payload)

    monkeypatch.setattr(provider, '_post', post)
    assert provider.guard_input('What is paracetamol?', [], None)['action'] == 'allow'
    assert provider.guard_output('question', 'answer', 'health')['allowed'] is True
    assert all((call['generationConfig']['temperature'] if kind == 'gemini'
                else call['temperature']) == 0 for call in calls)
    assert all(call.get('response_format', {}).get('type') == 'json_object'
               if kind == 'compatible' else call['generationConfig']['responseMimeType'] == 'application/json'
               for call in calls)


def test_provider_setting_routes_openrouter(settings):
    settings.ai_provider = 'openrouter'
    settings.openrouter_api_key = 'secret'
    settings.openrouter_model = 'openrouter/test'
    settings.ai_base_url = 'https://openrouter.ai/api/v1'
    from app.providers import configured_provider
    provider, config = configured_provider(settings)
    assert provider.name == 'openrouter'
    assert config.model == 'openrouter/test'
