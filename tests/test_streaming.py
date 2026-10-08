from contextlib import contextmanager
import json
from uuid import uuid4

import httpx
import pytest

from app.key_pool import KeyPool, PoolPolicy
from app.streaming import preview, stream_sink


def frames(response):
    return [(frame.splitlines()[0].removeprefix('event: '),
             json.loads(frame.split('data: ', 1)[1]))
            for frame in response.text.strip().split('\n\n') if frame.startswith('event:')]


def configure_provider(app):
    settings = app.state.service.graph.settings
    settings.ai_provider = 'pool'
    settings.openrouter_api_keys = 'stream-test-' + str(uuid4())
    settings.openrouter_model = 'test-model'


def mock_transport(monkeypatch, complete=True):
    calls = []
    monkeypatch.setattr(httpx, 'post', lambda url, **kwargs: httpx.Response(200,
        request=httpx.Request('POST', url), json={'choices': [{'message': {'content': '{"ids":[0]}'}}]}))
    @contextmanager
    def stream(method, url, **kwargs):
        calls.append(kwargs)
        packets = [
            {'choices': [{'delta': {'content': 'Appointments are booked '}, 'finish_reason': None}]},
            {'choices': [{'delta': {'content': 'through the patient portal [S1].'}, 'finish_reason': None}]},
        ]
        if complete:
            packets.append({'choices': [{'delta': {}, 'finish_reason': 'stop'}]})
        data = ''.join('data: ' + json.dumps(packet) + '\n\n' for packet in packets)
        yield httpx.Response(200, request=httpx.Request(method, url), content=data,
                             headers={'content-type': 'text/event-stream'})
    monkeypatch.setattr(httpx, 'stream', stream)
    return calls


def test_stream_delivers_drafts_and_persists_only_final_answer(client, app, monkeypatch):
    configure_provider(app)
    calls = mock_transport(monkeypatch)
    payload = {'message': 'How can I book an appointment?', 'request_id': str(uuid4())}
    response = client.post('/api/chat/stream', json=payload)
    assert response.headers['content-type'].startswith('text/event-stream')
    events = frames(response)
    assert events[0][0] == 'status'
    drafts = [data['text'] for name, data in events if name == 'draft' and data['text']]
    assert drafts[0] == 'Appointments are booked'
    assert 'portal' in drafts[-1]
    assert events[-1][0] == 'done'
    assert events[-1][1]['sources']
    assert calls[0]['json']['stream'] is True
    assert client.get('/api/history').json()['turns'][0]['answer'] == events[-1][1]
    replay = client.post('/api/chat/stream', json=payload)
    assert frames(replay)[-1] == events[-1]
    assert len(calls) == 1


def test_interrupted_provider_stream_reports_error_without_saving(client, app, monkeypatch):
    configure_provider(app)
    mock_transport(monkeypatch, complete=False)
    response = client.post('/api/chat/stream', json={
        'message': 'How can I book an appointment?', 'request_id': str(uuid4())})
    events = frames(response)
    assert events[-1][0] == 'error'
    assert events[-1][1]['code'] == 'LLM_UNAVAILABLE'
    assert events[-1][1]['status'] == 503
    assert client.get('/api/history').json()['turns'] == []


@pytest.mark.parametrize('format', ['gemini', 'compatible'])
def test_stream_key_rotation_preserves_reservation_and_cooldown(monkeypatch, format):
    pool = KeyPool(('first', 'second'), PoolPolicy())
    calls, updates = [], []
    @contextmanager
    def stream(method, url, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            yield httpx.Response(429, request=httpx.Request(method, url), headers={'Retry-After': '60'})
        else:
            packet = {'candidates': [{'content': {'parts': [{'text': 'Safe educational answer.'}]}, 'finishReason': 'STOP'}]} if format == 'gemini' else {
                'choices': [{'delta': {'content': 'Safe educational answer.'}, 'finish_reason': 'stop'}]}
            yield httpx.Response(200, request=httpx.Request(method, url), content='data: ' + json.dumps(packet) + '\n\n')
    monkeypatch.setattr(httpx, 'stream', stream)
    token = stream_sink.set(lambda name, data: updates.append(data))
    try:
        response = pool.post('https://example.com', stream_format=format)
    finally:
        stream_sink.reset(token)
    assert response.status_code == 200
    assert len(calls) == 2
    assert pool.states[0].cooldown_until > pool.clock()
    assert all(state.in_flight == 0 for state in pool.states)
    assert updates[-1]['text'] == 'Safe educational answer.'


def test_preview_does_not_expose_reasoning_or_disallowed_advice():
    assert preview('<think>private reasoning') == ''
    assert preview('<think>private reasoning</think>General education answer.') == 'General education answer.'
    assert preview('You should take 100 mg immediately.') == ''


def test_draft_is_emitted_before_upstream_finishes(monkeypatch):
    updates = []
    class Chunks(httpx.SyncByteStream):
        def __iter__(self):
            yield b'data: {"choices":[{"delta":{"content":"Educational answer starts "},"finish_reason":null}]}\n\n'
            assert updates[-1]['text'] == 'Educational answer starts'
            yield b'data: {"choices":[{"delta":{"content":"here."},"finish_reason":"stop"}]}\n\n'
    @contextmanager
    def stream(method, url, **kwargs):
        yield httpx.Response(200, request=httpx.Request(method, url), stream=Chunks())
    monkeypatch.setattr(httpx, 'stream', stream)
    token = stream_sink.set(lambda name, data: updates.append(data))
    try:
        response = KeyPool(('timing-key',), PoolPolicy()).post('https://example.com', stream_format='compatible')
    finally:
        stream_sink.reset(token)
    assert response.json()['choices'][0]['message']['content'] == 'Educational answer starts here.'
