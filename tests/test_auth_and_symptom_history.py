from uuid import uuid4
import pytest


def test_user_registration_and_login(client):
    # 1. Register a new patient
    reg_data = {
        'name': 'Ramesh Kumar',
        'email': 'ramesh@example.com',
        'password': 'password123',
        'phone': '+91 9876543210',
        'age': 52,
        'gender': 'Male',
        'emergency_contact': 'Sunita Kumar (Wife)',
        'emergency_phone': '+91 9123456789',
    }
    resp = client.post('/api/auth/register', json=reg_data)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert 'token' in data
    assert data['user']['email'] == 'ramesh@example.com'
    assert data['user']['name'] == 'Ramesh Kumar'
    assert data['user']['age'] == 52
    assert data['user']['emergency_contact'] == 'Sunita Kumar (Wife)'

    # 2. Check /api/auth/me using cookie
    me_resp = client.get('/api/auth/me')
    assert me_resp.status_code == 200
    assert me_resp.json()['user']['email'] == 'ramesh@example.com'

    # 3. Duplicate email should fail
    dup_resp = client.post('/api/auth/register', json=reg_data)
    assert dup_resp.status_code == 400
    assert 'already exists' in dup_resp.json()['error']

    # 4. Logout
    logout_resp = client.post('/api/auth/logout')
    assert logout_resp.status_code == 200

    # 5. /api/auth/me should now be None
    me_after = client.get('/api/auth/me')
    assert me_after.status_code == 200
    assert me_after.json()['user'] is None

    # 6. Login again
    login_resp = client.post('/api/auth/login', json={'email': 'ramesh@example.com', 'password': 'password123'})
    assert login_resp.status_code == 200
    assert login_resp.json()['user']['name'] == 'Ramesh Kumar'

    # 7. Wrong password fails
    bad_login = client.post('/api/auth/login', json={'email': 'ramesh@example.com', 'password': 'wrongpassword'})
    assert bad_login.status_code == 401


def test_emergency_alert_attaches_patient_identity(client, staff):
    # Register patient with emergency contact
    reg_data = {
        'name': 'Priya Sharma',
        'email': 'priya@example.com',
        'password': 'securepassword',
        'phone': '+91 9988776655',
        'age': 34,
        'gender': 'Female',
        'emergency_contact': 'Rahul Sharma (Brother)',
        'emergency_phone': '+91 9911223344',
    }
    client.post('/api/auth/register', json=reg_data)

    # Patient triggers emergency chat
    chat_resp = client.post('/api/chat', json={
        'message': 'Help me, severe chest pain radiating to left arm and cannot breathe',
        'request_id': str(uuid4())
    })
    assert chat_resp.status_code == 200
    answer = chat_resp.json()
    assert answer['route'] == 'emergency'
    review_id = answer['review_id']
    assert review_id is not None

    # Check staff review queue has patient identity
    reviews_resp = client.get('/api/staff/reviews', headers=staff)
    assert reviews_resp.status_code == 200
    reviews = reviews_resp.json()

    review = next((r for r in reviews if r['id'] == review_id), None)
    assert review is not None
    assert review['status'] == 'urgent'
    assert review['severity'] == 'emergency'
    assert 'Priya Sharma' in review['question']
    # Check attached patient user metadata
    assert review['user'] is not None
    assert review['user']['name'] == 'Priya Sharma'
    assert review['user']['email'] == 'priya@example.com'
    assert review['user']['phone'] == '+91 9988776655'
    assert review['user']['emergency_contact'] == 'Rahul Sharma (Brother)'
    assert review['user']['emergency_phone'] == '+91 9911223344'


def test_doctor_patient_symptom_summary_interval(client, staff):
    # Register patient
    reg_data = {
        'name': 'Arun Verma',
        'email': 'arun@example.com',
        'password': 'password123',
        'phone': '+91 9811223344',
        'age': 40,
    }
    client.post('/api/auth/register', json=reg_data)

    # Patient reports symptoms across multiple interactions
    # 1. Mild symptom
    client.post('/api/chat', json={
        'message': 'What causes a mild dry cough?',
        'request_id': str(uuid4())
    })

    # 2. Severe symptom
    client.post('/api/chat', json={
        'message': 'High fever for 4 days and severe headache',
        'request_id': str(uuid4())
    })

    # 3. Emergency symptom
    client.post('/api/chat', json={
        'message': 'Chest pain and crushing pressure on chest',
        'request_id': str(uuid4())
    })

    # Timeline summaries are available only in the staff dashboard.
    user_summary_resp = client.get('/api/user/symptom-history?since_days=30')
    assert user_summary_resp.status_code == 404

    # Doctor / staff checks the patient summary by email
    doctor_summary_resp = client.get('/api/staff/patient-summary?email=arun@example.com&since_days=14', headers=staff)
    assert doctor_summary_resp.status_code == 200
    doc_summary = doctor_summary_resp.json()

    assert doc_summary['patient']['name'] == 'Arun Verma'
    assert doc_summary['total_events'] == 3
    assert doc_summary['peak_severity'] == 'emergency'
    assert len(doc_summary['timeline']) == 3

    # Check staff patient directory endpoint
    patients_resp = client.get('/api/staff/patients', headers=staff)
    assert patients_resp.status_code == 200
    patients = patients_resp.json()['patients']
    assert any(p['email'] == 'arun@example.com' for p in patients)
