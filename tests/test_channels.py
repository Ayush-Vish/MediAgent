import hashlib
import hmac
import json

import httpx
from fastapi.testclient import TestClient

from app.main import create_app


def telegram_app(settings):
    settings.telegram_bot_token = 'fake-token'
    settings.telegram_webhook_secret = 'test-webhook-secret'
    settings.channel_hash_secret = 'x' * 32
    return create_app(settings)


def update(event_id=1):
    return {'update_id': event_id, 'message': {'chat': {'id': 12345, 'type': 'private'}, 'text': 'How do I book an appointment?'}}


def test_telegram_signature_and_dedup(settings, monkeypatch):
    app = telegram_app(settings)
    sent = []
    monkeypatch.setattr(app.state.channels, 'send', lambda *args: sent.append(args))
    with TestClient(app) as client:
        assert client.post('/webhooks/telegram', json=update()).status_code == 403
        headers = {'x-telegram-bot-api-secret-token': 'test-webhook-secret'}
        assert client.post('/webhooks/telegram', json=update(), headers=headers).status_code == 200
        assert client.post('/webhooks/telegram', json=update(), headers=headers).status_code == 200
    assert len(sent) == 1
    assert '12345' not in json.dumps(app.state.store.list('event'))


def test_failed_send_retries_without_regenerating(settings, monkeypatch):
    app = telegram_app(settings)
    attempts = []

    def send(*args):
        attempts.append(args)
        if len(attempts) == 1:
            raise httpx.ConnectError('offline')

    monkeypatch.setattr(app.state.channels, 'send', send)
    with TestClient(app, raise_server_exceptions=False) as client:
        headers = {'x-telegram-bot-api-secret-token': 'test-webhook-secret'}
        assert client.post('/webhooks/telegram', json=update(), headers=headers).status_code == 503
        assert client.post('/webhooks/telegram', json=update(), headers=headers).status_code == 200
        assert len(app.state.store.list('session')[0]['turns']) == 1
    assert attempts[0] == attempts[1]


def test_whatsapp_disabled(client):
    assert client.get('/webhooks/whatsapp').status_code == 404
    assert client.post('/webhooks/whatsapp', json={}).status_code == 404


def test_whatsapp_signature_and_verify(settings, monkeypatch):
    settings.whatsapp_enabled = True
    settings.whatsapp_access_token = 'fake'
    settings.whatsapp_phone_id = '123'
    settings.whatsapp_app_secret = 'app-secret'
    settings.whatsapp_verify_token = 'verify'
    settings.whatsapp_graph_version = 'v23.0'
    settings.channel_hash_secret = 'x' * 32
    app = create_app(settings)
    monkeypatch.setattr(app.state.channels, 'send', lambda *args: None)
    with TestClient(app) as client:
        assert client.get('/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=verify&hub.challenge=abc').text == 'abc'
        assert client.post('/webhooks/whatsapp', json={}).status_code == 403
        payload = json.dumps({'entry': []}).encode()
        signature = 'sha256=' + hmac.new(b'app-secret', payload, hashlib.sha256).hexdigest()
        assert client.post('/webhooks/whatsapp', content=payload, headers={'x-hub-signature-256': signature}).status_code == 200


def test_long_channel_message_is_not_silently_truncated(settings, monkeypatch):
    app = telegram_app(settings)
    sent = []
    monkeypatch.setattr(app.state.channels, 'send', lambda *args: sent.append(args))
    payload = update()
    payload['message']['text'] = 'x' * 2001
    with TestClient(app) as client:
        result = client.post('/webhooks/telegram', json=payload,
            headers={'x-telegram-bot-api-secret-token': 'test-webhook-secret'})
        assert result.status_code == 200
        assert '2,000 characters' in sent[0][2]
        assert app.state.store.list('session')[0]['turns'] == []


def test_channel_commands_are_rate_limited(settings, monkeypatch):
    app = telegram_app(settings)
    sent = []
    monkeypatch.setattr(app.state.channels, 'send', lambda *args: sent.append(args))
    for i in range(21):
        app.state.channels.process('telegram', str(i), '12345', '/status')
    assert len(sent) == 20
