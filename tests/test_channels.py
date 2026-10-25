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


def telegram_post(client, event_id, text):
    payload = update(event_id)
    payload['message']['text'] = text
    return client.post('/webhooks/telegram', json=payload,
                       headers={'x-telegram-bot-api-secret-token': 'test-webhook-secret'})


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
        # Consent is accepted and delivered before testing retry of an AI answer.
        attempts.append(('setup',))
        assert telegram_post(client, 1, '/accept').status_code == 200
        attempts.clear()
        assert telegram_post(client, 2, 'How do I book an appointment?').status_code == 503
        assert telegram_post(client, 2, 'How do I book an appointment?').status_code == 200
        assert len(app.state.store.list('session')[0]['turns']) == 1
    assert attempts[0] == attempts[1]


def test_telegram_consent_patient_visibility_and_doctor_delivery(settings, monkeypatch):
    app = telegram_app(settings)
    sent = []
    monkeypatch.setattr(app.state.channels, 'send', lambda *args: sent.append(args))
    with TestClient(app) as client:
        assert telegram_post(client, 1, '/start').status_code == 200
        assert 'Privacy notice' in sent[-1][2]
        assert app.state.store.list('user') == []

        assert telegram_post(client, 2, '/accept').status_code == 200
        assert telegram_post(client, 3, 'How do I book an appointment?').status_code == 200
        patient = client.get('/api/staff/patients',
                             headers={'Authorization': 'Bearer test-staff-token'}).json()['patients'][0]
        history = client.get(f"/api/staff/patients/{patient['id']}/messages",
                             headers={'Authorization': 'Bearer test-staff-token'}).json()['messages']
        assert [item['role'] for item in history] == ['user', 'assistant']

        reply = {'conversation_id': history[0]['conversation_id'], 'text': 'Please bring your appointment letter.',
                 'request_id': '3d594650-3436-4d5d-8d77-0fd4ff7c6894'}
        assert client.post(f"/api/staff/patients/{patient['id']}/messages", json=reply,
                           headers={'Authorization': 'Bearer test-staff-token'}).status_code == 200
        assert sent[-1] == ('telegram', '12345', 'Doctor / staff:\nPlease bring your appointment letter.')
        delivery_count = len(sent)
        assert client.post(f"/api/staff/patients/{patient['id']}/messages", json=reply,
                           headers={'Authorization': 'Bearer test-staff-token'}).status_code == 200
        assert len(sent) == delivery_count

        stored = json.dumps(app.state.store.list('channel_binding'))
        assert '12345' not in stored
        assert 'recipient' in stored


def test_telegram_forget_deletes_retained_patient_data(settings, monkeypatch):
    app = telegram_app(settings)
    monkeypatch.setattr(app.state.channels, 'send', lambda *args: None)
    with TestClient(app) as client:
        telegram_post(client, 1, '/accept')
        telegram_post(client, 2, 'How do I book an appointment?')
        assert app.state.store.list('patient_message')
        telegram_post(client, 3, '/forget')
        assert app.state.store.list('patient_message') == []
        assert app.state.store.list('channel_binding') == []
        assert app.state.store.list('user') == []


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
        assert app.state.store.list('session') == []


def test_channel_commands_are_rate_limited(settings, monkeypatch):
    app = telegram_app(settings)
    sent = []
    monkeypatch.setattr(app.state.channels, 'send', lambda *args: sent.append(args))
    for i in range(21):
        app.state.channels.process('telegram', str(i), '12345', '/status')
    assert len(sent) == 20
