"""High-performance Hybrid RAG engine with BAAI/bge-small-en-v1.5, BM25, and BAAI/bge-reranker-base.

Implements the two-phase pipeline from the architecture specification:
1. Ingestion: Hierarchical extraction, chunking, dual dense (BGE) + sparse (BM25) vectorization,
   JSON metadata generation, and multi-vector Qdrant Point storage.
2. Retrieval: Dual query encoding, Cosine + BM25 hybrid search with Reciprocal Rank Fusion (RRF),
   adjacent chunk context window expansion, and BAAI/bge-reranker-base cross-encoder reranking.
"""
from __future__ import annotations

import logging
from app.observability import traced, span, event
from dataclasses import asdict
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import QdrantClient, models

from app.chunker import chunk_document
from app.config import ROOT

log = logging.getLogger(__name__)

DENSE_MODEL = 'BAAI/bge-small-en-v1.5'
SPARSE_MODEL = 'Qdrant/bm25'
RERANKER_MODEL = 'BAAI/bge-reranker-base'
DENSE_DIM = 384


class HybridIndex:
    def __init__(self, settings, client=None):
        from fastembed import SparseTextEmbedding, TextEmbedding
        self.settings = settings
        if client:
            self.client = client
        elif settings.qdrant_url:
            self.client = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key or None, timeout=10)
        elif settings.qdrant_path:
            self.client = QdrantClient(path=str(ROOT / settings.qdrant_path))
        else:
            # Local in-memory or on-disk fallback when no remote Qdrant URL is configured
            self.client = QdrantClient(':memory:')

        self.dense = TextEmbedding(DENSE_MODEL, threads=1)
        self.sparse = SparseTextEmbedding(SPARSE_MODEL, threads=1)
        self.reranker = None
        if getattr(settings, 'rerank_enabled', True):
            try:
                from fastembed.rerank.cross_encoder import TextCrossEncoder
                self.reranker = TextCrossEncoder(RERANKER_MODEL, threads=1)
            except Exception as e:
                log.warning('Failed to load cross-encoder reranker %s: %s', RERANKER_MODEL, e)

    def ensure_collection(self):
        name = self.settings.qdrant_collection
        if not self.client.collection_exists(name):
            self.client.create_collection(
                name,
                vectors_config={'dense': models.VectorParams(size=DENSE_DIM, distance=models.Distance.COSINE)},
                sparse_vectors_config={'sparse': models.SparseVectorParams(modifier=models.Modifier.IDF)}
            )
        if self.settings.qdrant_url:
            for field in ('hospital_id', 'category', 'doc_id', 'source_id', 'chunk_id'):
                try:
                    self.client.create_payload_index(name, field, models.PayloadSchemaType.KEYWORD)
                except Exception:
                    pass

    def index(self, sources):
        """Ingest sources with chunking, metadata generation, and dual vectorization."""
        self.ensure_collection()
        name = self.settings.qdrant_collection
        points = []

        for source in sources:
            source_id = source['id']
            title = source.get('title', '')
            url = str(source.get('url', ''))
            category = source.get('category', 'health')
            hospital_id = source.get('hospital_id', self.settings.hospital_id)
            checked_at = str(source.get('checked_at', ''))
            full_text = source.get('text', '')

            # Chunk document with adjacent chunk metadata
            doc_chunks = chunk_document(
                doc_id=source_id,
                title=title,
                text=full_text,
                category=category,
                hospital_id=hospital_id,
                url=url,
                checked_at=checked_at
            )

            chunk_texts = [f"{c.metadata.title}\n{c.text}" for c in doc_chunks]
            dense_vectors = [v.tolist() for v in self.dense.embed(chunk_texts)]
            sparse_vectors = list(self.sparse.embed(chunk_texts))

            for chunk, dense_vec, sparse_vec in zip(doc_chunks, dense_vectors, sparse_vectors):
                # When a source has only 1 chunk, map point ID directly to source_id for compatibility
                point_key = source_id if len(doc_chunks) == 1 else chunk.chunk_id
                point_id = str(uuid5(NAMESPACE_URL, point_key))
                point = models.PointStruct(
                    id=point_id,
                    vector={
                        'dense': dense_vec,
                        'sparse': models.SparseVector(
                            indices=sparse_vec.indices.tolist(),
                            values=sparse_vec.values.tolist()
                        )
                    },
                    payload={
                        'source_id': source_id,
                        'doc_id': chunk.metadata.doc_id,
                        'chunk_id': chunk.chunk_id,
                        'title': title,
                        'url': url,
                        'category': category,
                        'hospital_id': hospital_id,
                        'text': chunk.text,
                        'metadata': asdict(chunk.metadata)
                    }
                )
                points.append(point)

        # Batch upsert points
        if points:
            for i in range(0, len(points), 50):
                self.client.upsert(name, points[i:i + 50], wait=True)

    @traced('vector.search')
    def search_candidates(self, query: str, category: str, limit: int = 8) -> list[models.ScoredPoint]:
        """Hybrid search with Cosine + BM25 using Reciprocal Rank Fusion (RRF)."""
        self.ensure_collection()
        name = self.settings.qdrant_collection
        try:
            if self.client.count(name).count == 0:
                return []
        except Exception:
            return []

        with span('vector.query_embedding'):
            dense_query = next(self.dense.query_embed(query)).tolist()
            sparse_query = next(self.sparse.query_embed(query))

        scope = models.Filter(must=[
            models.FieldCondition(key='hospital_id', match=models.MatchValue(value=self.settings.hospital_id)),
            models.FieldCondition(key='category', match=models.MatchValue(value=category))
        ])

        prefetch_limit = max(limit * 3, 15)
        response = self.client.query_points(
            name,
            prefetch=[
                models.Prefetch(query=dense_query, using='dense', filter=scope, limit=prefetch_limit),
                models.Prefetch(
                    query=models.SparseVector(
                        indices=sparse_query.indices.tolist(),
                        values=sparse_query.values.tolist()
                    ),
                    using='sparse',
                    filter=scope,
                    limit=prefetch_limit
                )
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=limit,
            with_payload=True
        )
        return response.points

    @traced('vector.expand')
    def expand_adjacent_chunks(self, candidate_points: list[models.ScoredPoint]) -> list[dict[str, Any]]:
        """Fetch adjacent chunks (prev/next) from Qdrant to assemble complete context windows."""
        name = self.settings.qdrant_collection
        expanded_chunks = []

        neighbor_ids_map = {}
        all_neighbor_point_ids = set()

        for pt in candidate_points:
            meta = pt.payload.get('metadata', {})
            prev_id = meta.get('prev_chunk_id')
            next_id = meta.get('next_chunk_id')
            n_ids = []
            if prev_id:
                uuid_prev = str(uuid5(NAMESPACE_URL, prev_id))
                n_ids.append(uuid_prev)
                all_neighbor_point_ids.add(uuid_prev)
            if next_id:
                uuid_next = str(uuid5(NAMESPACE_URL, next_id))
                n_ids.append(uuid_next)
                all_neighbor_point_ids.add(uuid_next)
            neighbor_ids_map[pt.id] = n_ids

        fetched_neighbors = {}
        if all_neighbor_point_ids:
            try:
                retrieved = self.client.retrieve(name, ids=list(all_neighbor_point_ids), with_payload=True)
                for r in retrieved:
                    fetched_neighbors[r.id] = r
            except Exception as e:
                log.warning('Could not fetch adjacent chunks: %s', e)

        for pt in candidate_points:
            meta = pt.payload.get('metadata', {})
            primary_text = pt.payload.get('text', '')
            primary_idx = meta.get('chunk_index', 0)

            # Build stitched text in sequential order
            neighbor_pts = [fetched_neighbors[nid] for nid in neighbor_ids_map.get(pt.id, []) if nid in fetched_neighbors]
            all_pts = [pt] + neighbor_pts
            all_pts.sort(key=lambda p: p.payload.get('metadata', {}).get('chunk_index', primary_idx))

            stitched_text = "\n\n".join(p.payload.get('text', '') for p in all_pts)
            expanded_chunks.append({
                'point': pt,
                'source_id': pt.payload.get('source_id'),
                'title': pt.payload.get('title'),
                'url': pt.payload.get('url'),
                'category': pt.payload.get('category'),
                'text': primary_text,
                'expanded_text': stitched_text,
                'metadata': meta,
                'score': getattr(pt, 'score', 0.0)
            })

        return expanded_chunks

    @traced('vector.rerank')
    def rerank_passages(self, query: str, candidate_chunks: list[dict[str, Any]], top_k: int = 4) -> list[dict[str, Any]]:
        """Rerank candidate passages with BAAI/bge-reranker-base cross-encoder."""
        if not candidate_chunks or not self.reranker:
            event('vector.rerank.skipped', reason='empty_candidates' if not candidate_chunks else 'disabled')
            return candidate_chunks[:top_k]

        passages = [c['expanded_text'] for c in candidate_chunks]
        scores = list(self.reranker.rerank(query, passages))

        for chunk, score in zip(candidate_chunks, scores):
            chunk['rerank_score'] = float(score)

        candidate_chunks.sort(key=lambda c: c['rerank_score'], reverse=True)
        return candidate_chunks[:top_k]

    @traced('vector.pipeline')
    def search_hybrid_pipeline(self, query: str, category: str, top_k: int = 4) -> list[dict[str, Any]]:
        """End-to-end retrieval: Hybrid Search -> (Top k * 2) -> Adjacent Expansion -> Cross-Encoder Rerank -> Top k."""
        candidates = self.search_candidates(query, category, limit=top_k * 2)
        if not candidates:
            return []

        expanded = self.expand_adjacent_chunks(candidates)
        refined = self.rerank_passages(query, expanded, top_k=top_k)
        return refined

    def search(self, query, category):
        """Compatible entry point returning unique source IDs for backward compatibility."""
        refined = self.search_hybrid_pipeline(query, category, top_k=4)
        seen = set()
        unique_ids = []
        for r in refined:
            sid = r['source_id']
            if sid not in seen:
                seen.add(sid)
                unique_ids.append(sid)
        return unique_ids
