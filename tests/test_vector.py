"""Exercise the actual Qdrant wire models using local storage and synthetic vectors.

This checks filtering, upserts and fusion, not embedding quality or cloud access.
"""
from types import SimpleNamespace

import numpy as np
from qdrant_client import QdrantClient

from app.vector import HybridIndex


class DenseFixture:
    def embed(self, texts):
        for _ in texts:
            yield np.array([1.0] + [0.0] * 383)

    def query_embed(self, query):
        return self.embed([query])


class SparseFixture:
    def embed(self, texts):
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
