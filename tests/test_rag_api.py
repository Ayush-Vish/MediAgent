"""Test the FastAPI RAG microservice endpoints:
1. POST /api/rag/embed
2. POST /api/rag/rerank
3. POST /api/rag/search
"""


def test_api_rag_embed(client):
    response = client.post('/api/rag/embed', json={'texts': ['Vertigo symptoms', 'Epley maneuver']})
    assert response.status_code == 200
    data = response.json()
    assert 'dense' in data
    assert 'sparse' in data
    assert len(data['dense']) == 2
    assert len(data['dense'][0]) == 384  # BAAI/bge-small-en-v1.5 dimensions
    assert len(data['sparse']) == 2


def test_api_rag_rerank(client):
    query = "What causes benign paroxysmal positional vertigo?"
    passages = [
        "Benign paroxysmal positional vertigo (BPPV) is caused by dislodged calcium crystals in the inner ear.",
        "The hospital billing department is open Monday through Friday from 8 AM to 5 PM."
    ]
    response = client.post('/api/rag/rerank', json={
        'query': query,
        'passages': passages,
        'top_k': 2
    })
    assert response.status_code == 200
    data = response.json()
    assert 'ranked' in data
    assert len(data['ranked']) == 2
    # The clinical passage about BPPV should rank higher than hospital billing
    assert data['ranked'][0]['index'] == 0


def test_api_rag_search(client):
    response = client.post('/api/rag/search', json={
        'query': 'How to book an appointment?',
        'category': 'hospital',
        'top_k': 3
    })
    assert response.status_code == 200
    data = response.json()
    assert 'results' in data
