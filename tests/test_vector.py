"""Exercise the actual Qdrant wire models using local storage and synthetic vectors.

This checks filtering, upserts and fusion, not embedding quality or cloud access.
"""
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
import time

import numpy as np
from qdrant_client import QdrantClient

from app.vector import HybridIndex


def test_hybrid_models_initialize_once_across_concurrent_requests(app, monkeypatch):
    knowledge = app.state.knowledge
    created = []

    class FakeIndex:
        def __init__(self, settings):
            created.append(settings)
            time.sleep(0.05)

    monkeypatch.setattr('app.vector.HybridIndex', FakeIndex)
    with ThreadPoolExecutor(max_workers=4) as pool:
        indexes = list(pool.map(lambda _: knowledge.hybrid_index(), range(4)))

    assert len(created) == 1
    assert all(index is indexes[0] for index in indexes)


class DenseFixture:
    def embed(self, texts, **kwargs):
        for _ in texts:
            yield np.array([1.0] + [0.0] * 383)

    def query_embed(self, query):
        return self.embed([query])


class SparseFixture:
    def embed(self, texts, **kwargs):
        for _ in texts:
            yield SimpleNamespace(indices=np.array([1, 3]), values=np.array([1.0, 0.5]))

    def query_embed(self, query):
        return self.embed([query])


def test_real_qdrant_upsert_fusion_and_scope(settings):
    index = HybridIndex.__new__(HybridIndex)
    index.settings = settings
    index.client = QdrantClient(':memory:')
    index.dense, index.sparse, index.reranker = DenseFixture(), SparseFixture(), None
    sources = [
        {'id': 'one', 'title': 'Appointments', 'text': 'Visit the booking portal.',
         'hospital_id': 'cmc-vellore', 'category': 'hospital'},
        {'id': 'two', 'title': 'Health', 'text': 'General health education.',
         'hospital_id': 'cmc-vellore', 'category': 'health'},
        {'id': 'three', 'title': 'Other hospital', 'text': 'Other booking portal.',
         'hospital_id': 'other', 'category': 'hospital'},
    ]
    try:
        index.index(sources)
        index.index(sources)
        assert index.client.count(settings.qdrant_collection).count == 3
        assert index.search('booking', 'hospital') == ['one']
        assert index.search('education', 'health') == ['two']
    finally:
        index.client.close()


def test_pending_points_are_uploaded_but_not_returned_to_patients(settings):
    index = HybridIndex.__new__(HybridIndex)
    index.settings = settings
    index.client = QdrantClient(':memory:')
    index.dense, index.sparse, index.reranker = DenseFixture(), SparseFixture(), None
    try:
        count = index.index([
            {'id': 'reviewed', 'title': 'Medicine', 'text': 'Reviewed general medicine information.',
             'category': 'health', 'approved': True},
            {'id': 'pending', 'title': 'Medicine', 'text': 'Pending FDA medicine information.',
             'category': 'health', 'approved': False},
        ], pre_chunked=True)
        assert count == 2
        assert index.client.count(settings.qdrant_collection).count == 2
        assert index.search('medicine', 'health') == ['reviewed']
    finally:
        index.client.close()


def test_index_streams_uploads_before_consuming_entire_corpus(settings):
    index = HybridIndex.__new__(HybridIndex)
    index.settings = settings
    index.client = QdrantClient(':memory:')
    index.dense, index.sparse, index.reranker = DenseFixture(), SparseFixture(), None
    def sources():
        for number in range(130):
            if number == 64:
                assert index.client.count(settings.qdrant_collection).count >= 64
            yield {'id': str(number), 'title': 'Medicine', 'text': 'Public medicine information.', 'category': 'health'}
    try:
        assert index.index(sources(), pre_chunked=True) == 130
        assert index.index(sources(), pre_chunked=True) == 130
        assert index.client.count(settings.qdrant_collection).count == 130
    finally:
        index.client.close()
