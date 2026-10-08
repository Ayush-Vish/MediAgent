import json
from uuid import uuid4

import httpx
import pytest

from app.providers import GeminiProvider, OpenAICompatibleProvider, ProviderConfig


def capture_context(app, monkeypatch):
    contexts = []
    original = app.state.service.graph.run
    def run(query, previous_topic=None, context=None):
        contexts.append(context)
        return original(query, previous_topic, context)
    monkeypatch.setattr(app.state.service.graph, 'run', run)
    return contexts


def test_anonymous_context_uses_last_three_messages_and_reset(client, app, monkeypatch):
    contexts = capture_context(app, monkeypatch)
    ids = []
    for query in ['hello', 'hi', 'hello']:
        request_id = str(uuid4())
        ids.append(request_id)
        assert client.post('/api/chat', json={'message': query, 'request_id': request_id}).status_code == 200
    assert contexts[0] == []
    assert [m['role'] for m in contexts[1]] == ['user', 'assistant']
    assert [m['role'] for m in contexts[2]] == ['assistant', 'user', 'assistant']
    assert contexts[2][1]['text'] == 'hi'
    # A replay does not add duplicate history or invoke the model again.
    client.post('/api/chat', json={'message': 'hello', 'request_id': ids[-1]})
    assert len(contexts) == 3
    client.delete('/api/session')
    client.post('/api/session')
    client.post('/api/chat', json={'message': 'hi', 'request_id': str(uuid4())})
    assert contexts[-1] == []


def test_signed_in_context_includes_doctor_message(client, app, staff, monkeypatch):
    patient = client.post('/api/auth/register', json={
        'name': 'Patient', 'email': 'context@example.com', 'password': 'secure-password',
    }).json()['user']
    contexts = capture_context(app, monkeypatch)
    client.post('/api/chat', json={'message': 'hello', 'request_id': str(uuid4())})
    path = f"/api/staff/patients/{patient['id']}/messages"
    messages = client.get(path, headers=staff).json()['messages']
    client.post(path, headers=staff, json={'conversation_id': messages[0]['conversation_id'],
        'text': 'Bring your appointment letter.', 'request_id': str(uuid4())})
    client.post('/api/chat', json={'message': 'hi', 'request_id': str(uuid4())})
    assert [m['role'] for m in contexts[-1]] == ['user', 'assistant', 'staff']
    assert contexts[-1][-1]['text'] == 'Bring your appointment letter.'


@pytest.mark.parametrize('kind', ['gemini', 'compatible'])
@pytest.mark.parametrize('grounded', [False, True])
def test_provider_payload_contains_query_context_and_rag(kind, grounded, monkeypatch):
    config = ProviderConfig('context-test-key', 'test-model', 100)
    provider = GeminiProvider(config) if kind == 'gemini' else OpenAICompatibleProvider(config, 'https://example.com/v1')
    calls = []
    def post(url, **kwargs):
        calls.append(kwargs['json'])
        return httpx.Response(200, request=httpx.Request('POST', url), json={
            'candidates': [{'content': {'parts': [{'text': 'Answer [S1]'}]}}],
            'choices': [{'message': {'content': 'Answer [S1]'}}],
        })
    monkeypatch.setattr(provider, '_post', post)
    context = [{'role': 'user', 'text': f'previous {i}'} for i in range(5)]
    sources = [{'title': 'Public information', 'text': 'Book an appointment online.',
                'url': 'https://example.com', 'checked_at': '2026-10-08'}]
    if grounded:
        provider.generate('what next?', 'hospital', sources, context)
    else:
        provider.generate_general('what next?', context)
    payload = calls[0]
    raw = payload['contents'][0]['parts'][0]['text'] if kind == 'gemini' else payload['messages'][-1]['content']
    prompt = json.loads(raw)
    assert prompt['question'] == 'what next?'
    assert prompt['recent_messages'] == context[-3:]
    if grounded:
        assert prompt['evidence'][0]['title'] == 'Public information'
