import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.safety import assess_severity


def test_assess_severity_emergency():
    res = assess_severity('I have sudden chest pain and trouble breathing')
    assert res.level == 'emergency'
    assert res.confidence >= 0.90
    assert 'cardiac' in res.summary.lower() or 'respiratory' in res.summary.lower()


def test_assess_severity_severe():
    res = assess_severity('I have high fever and severe headache for 4 days')
    assert res.level == 'severe'
    assert res.confidence >= 0.80
    assert 'fever' in res.summary.lower() or 'pain' in res.summary.lower()


def test_assess_severity_general_health():
    res = assess_severity('What causes a mild cough?')
    assert res.level == 'moderate'
    assert res.confidence < 0.70


def test_assess_severity_non_health():
    res = assess_severity('How do I get to the reception desk?')
    assert res.level == 'general'
    assert res.confidence == 0.0


def test_emergency_query_creates_staff_alert(client, app, staff):
    resp = client.post('/api/chat', json={
        'message': 'Help, I have severe chest pain and cannot breathe',
        'request_id': str(uuid4())
    })
    assert resp.status_code == 200
    answer = resp.json()
    assert answer['route'] == 'emergency'
    assert answer['severity']['level'] == 'emergency'
    assert answer['severity']['confidence'] >= 0.90
    assert answer['review_id'] is not None

    # Check that the staff queue received the alert
    reviews = client.get('/api/staff/reviews', headers=staff).json()
    alert_review = next((r for r in reviews if r['id'] == answer['review_id']), None)
    assert alert_review is not None
    assert alert_review['status'] == 'urgent'
    assert alert_review['severity'] == 'emergency'
    assert alert_review['confidence'] >= 0.90
    assert 'EMERGENCY' in alert_review['question']


def test_severe_symptom_creates_staff_alert_and_answers(client, app, staff):
    resp = client.post('/api/chat', json={
        'message': 'I have had a high fever for 5 days with severe headache',
        'request_id': str(uuid4())
    })
    assert resp.status_code == 200
    answer = resp.json()
    assert answer['severity']['level'] == 'severe'
    assert answer['severity']['confidence'] >= 0.80
    assert answer['review_id'] is not None
    assert 'Important' in answer['text'] or 'prompt medical attention' in answer['text']

    # Check staff reviews
    reviews = client.get('/api/staff/reviews', headers=staff).json()
    alert_review = next((r for r in reviews if r['id'] == answer['review_id']), None)
    assert alert_review is not None
    assert alert_review['severity'] == 'severe'
    assert alert_review['confidence'] >= 0.80


def test_mild_health_query_does_not_alert_staff(client, app, staff):
    # Clear prior reviews if any
    resp = client.post('/api/chat', json={
        'message': 'What is general cough education?',
        'request_id': str(uuid4())
    })
    assert resp.status_code == 200
    answer = resp.json()
    assert answer['route'] == 'health'
    # Mild query should not create an alert
    assert answer.get('review_id') is None
