import time
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import create_app
from tests.test_doctor_conversations import register


def imported_turn():
    return {'request_id': str(uuid4()), 'question': 'My earlier question in full',
            'answer_text': 'My earlier answer', 'created': time.time() - 10}


def test_import_requires_matching_account_and_is_idempotent(client, app, settings):
    turn = imported_turn()
    assert client.post('/api/history/import', json={'user_id': 'unknown', 'turns': [turn]}).status_code == 401
    patient = register(client)
    payload = {'user_id': patient['id'], 'turns': [turn]}
    first = client.post('/api/history/import', json=payload)
    assert first.status_code == 200
    assert [m['text'] for m in first.json()['messages']] == [turn['question'], turn['answer_text']]
    assert client.post('/api/history/import', json=payload).json()['messages'] == first.json()['messages']
    records = app.state.store.records
    with app.state.store.engine.connect() as conn:
        rows = conn.execute(records.select().where(records.c.kind.in_(['patient_message', 'patient_session']))).mappings()
        assert all(row['expires'] is None for row in rows)
    client.post('/api/auth/logout')
    assert client.cookies.get('mediagent_session') is None
    client.post('/api/session')
    assert client.get('/api/history').json()['messages'] == []
    restarted = create_app(settings)
    with TestClient(restarted) as other:
        login = other.post('/api/auth/login', json={'email': patient['email'], 'password': 'secure-password'})
        assert login.status_code == 200
        assert other.get('/api/history').json()['messages'] == first.json()['messages']


def test_account_switch_cannot_import_or_delete_another_patients_history(client, app):
    first = register(client)
    token = client.cookies.get('mediagent_session')
    payload = {'user_id': first['id'], 'turns': [imported_turn()]}
    assert client.post('/api/history/import', json=payload).status_code == 200
    register(client, 'second@example.com')
    assert client.post('/api/history/import', json=payload).status_code == 403
    client.cookies.clear()
    client.cookies.set('mediagent_session', token)
    assert client.delete('/api/session').status_code == 401
    assert len(app.state.service.patient_messages(first['id'])) == 2


def test_clear_removes_persistent_chat(client, app):
    patient = register(client)
    client.post('/api/chat', json={'message': 'hello', 'request_id': str(uuid4())})
    assert client.get('/api/history').json()['turns'][0]['question'] == 'hello'
    assert client.delete('/api/session').status_code == 200
    client.post('/api/session')
    assert client.get('/api/history').json()['messages'] == []
    assert app.state.service.patient_messages(patient['id']) == []


def test_import_rejects_invalid_content(client):
    patient = register(client)
    turn = imported_turn()
    turn['question'] = 'x' * 2001
    assert client.post('/api/history/import', json={'user_id': patient['id'], 'turns': [turn]}).status_code == 422


def test_import_recovers_partial_write_without_duplicates(client, app, monkeypatch):
    patient = register(client)
    payload = {'user_id': patient['id'], 'turns': [imported_turn()]}
    put = app.state.store.put

    def fail(key, kind, value, **kwargs):
        if kind == 'history_import':
            raise RuntimeError('Interrupted before receipt')
        return put(key, kind, value, **kwargs)

    monkeypatch.setattr(app.state.store, 'put', fail)
    import pytest
    with pytest.raises(RuntimeError):
        client.post('/api/history/import', json=payload)
    monkeypatch.setattr(app.state.store, 'put', put)
    result = client.post('/api/history/import', json=payload)
    assert result.status_code == 200
    assert len(result.json()['messages']) == 2
