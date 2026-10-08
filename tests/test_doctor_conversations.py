from uuid import uuid4


def register(client, email='patient@example.com'):
    return client.post('/api/auth/register', json={
        'name': 'Test Patient', 'email': email, 'password': 'secure-password',
    }).json()['user']


def test_doctor_reply_is_in_same_chat_and_idempotent(client, app, staff):
    patient = register(client)
    client.post('/api/chat', json={'message': 'hello', 'request_id': str(uuid4())})
    path = f"/api/staff/patients/{patient['id']}/messages"
    assert client.get(path).status_code == 401
    history = client.get(path, headers=staff).json()['messages']
    assert [m['role'] for m in history] == ['user', 'assistant']
    payload = {'conversation_id': history[0]['conversation_id'],
               'text': 'Please bring your appointment letter.', 'request_id': str(uuid4())}
    reply = client.post(path, headers=staff, json=payload)
    assert reply.status_code == 200
    assert client.post(path, headers=staff, json=payload).json()['id'] == reply.json()['id']
    messages = client.get('/api/history').json()['messages']
    assert messages[-1]['role'] == 'staff'
    assert messages[-1]['text'] == payload['text']
    assert len(messages) == 3
    other = register(client, 'other@example.com')
    wrong = client.post(f"/api/staff/patients/{other['id']}/messages", headers=staff, json=payload)
    assert wrong.status_code == 404
    assert client.get('/api/history').status_code == 401
    client.post('/api/session')
    assert client.get('/api/history').json()['messages'] == []


def test_ai_summary_requires_staff_and_calls_pool(client, app, staff):
    patient = register(client)
    client.post('/api/chat', json={'message': 'hello', 'request_id': str(uuid4())})
    calls = []
    class FakeProvider:
        def summarize(self, timeline):
            calls.append(timeline)
            return 'Reported a general inquiry. AI-generated draft; clinician review required.'
    app.state.service.graph.provider = FakeProvider()
    path = f"/api/staff/patients/{patient['id']}/summary"
    assert client.post(path, json={'since_days': 30}).status_code == 401
    result = client.post(path, headers=staff, json={'since_days': 30})
    assert result.status_code == 200
    assert result.json()['ai_generated'] is True
    assert len(calls) == 1
    assert patient['email'] not in calls[0]
    assert client.post(path, headers=staff, json={'since_days': 1000}).status_code == 422
    assert client.get('/api/user/symptom-history').status_code == 404
    assert 'admin_token' not in client.get('/api/config').json()
    assert client.get('/api/staff/patients', headers={'Authorization': 'Bearer admin'}).status_code == 401


def test_summary_provider_unavailable_is_frontend_error(client, app, staff):
    from app.llm_pool import LLMUnavailable
    patient = register(client)
    client.post('/api/chat', json={'message': 'hello', 'request_id': str(uuid4())})
    class Unavailable:
        def summarize(self, timeline):
            raise LLMUnavailable()
    app.state.service.graph.provider = Unavailable()
    response = client.post(f"/api/staff/patients/{patient['id']}/summary", headers=staff,
                           json={'since_days': 30})
    assert response.status_code == 503
    assert 'No AI model' in response.json()['error']
