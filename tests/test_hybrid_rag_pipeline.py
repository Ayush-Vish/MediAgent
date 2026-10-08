"""Test the complete Hybrid RAG Pipeline:
1. Ingestion: Extraction, chunking, metadata generation, dual sparse/dense vectorization.
2. Qdrant Hybrid Storage: Multi-vector point persistence.
3. Retrieval: Dual query encoding, Cosine + BM25 RRF fusion.
4. Adjacent Chunk Expansion: Context window stitching.
5. Cross-Encoder Reranking: BAAI/bge-reranker-base.
"""
from qdrant_client import QdrantClient

from app.chunker import chunk_document, extract_sections
from app.vector import HybridIndex


def test_chunk_document_adjacency_and_metadata():
    text = (
        "# Overview of Vertigo\n"
        "Vertigo is a sensation of feeling off balance. If you have these dizzy spells, "
        "you might feel like you are spinning or that the world around you is spinning.\n\n"
        "# Causes and Triggers\n"
        "Benign paroxysmal positional vertigo (BPPV) is one of the most common causes of vertigo. "
        "It causes brief episodes of mild to intense dizziness. It is usually triggered by specific changes in your head's position.\n\n"
        "# Treatment and Prevention\n"
        "Treatment for BPPV often includes a simple procedure called the canalith repositioning procedure or Epley maneuver."
    )
    chunks = chunk_document(
        doc_id="doc_vertigo_01",
        title="Vertigo Clinical Guide",
        text=text,
        category="health",
        hospital_id="cmc-vellore",
        url="https://example.org/vertigo",
        chunk_size=30,
        overlap=10
    )

    assert len(chunks) >= 3
    # Verify sequential adjacency pointers
    assert chunks[0].metadata.prev_chunk_id is None
    assert chunks[0].metadata.next_chunk_id == chunks[1].chunk_id
    assert chunks[1].metadata.prev_chunk_id == chunks[0].chunk_id
    assert chunks[-1].metadata.next_chunk_id is None

    for c in chunks:
        assert c.metadata.doc_id == "doc_vertigo_01"
        assert c.metadata.title == "Vertigo Clinical Guide"
        assert c.metadata.category == "health"
        assert c.metadata.hospital_id == "cmc-vellore"


def test_hybrid_index_pipeline_end_to_end(settings):
    # Enable reranker for this test
    test_settings = settings.model_copy(update={'rerank_enabled': True})
    memory_client = QdrantClient(':memory:')
    index = HybridIndex(test_settings, client=memory_client)

    sample_doc = {
        'id': 'doc_dizziness_101',
        'title': 'Dizziness and Vestibular Disorders',
        'url': 'https://medlineplus.gov/dizziness.html',
        'category': 'health',
        'hospital_id': 'cmc-vellore',
        'checked_at': '2026-10-07',
        'text': (
            "# Introduction to Dizziness\n"
            "Dizziness is a broad term used to describe a range of sensations, such as feeling faint, "
            "woozy, weak or unsteady. Dizziness that creates the false sense that you or your surroundings are spinning is called vertigo.\n\n"
            "# Common Causes\n"
            "Inner ear problems: Your sense of balance depends on the combined input from various parts of your sensory system. "
            "Circulation problems: You may feel dizzy, faint or off balance if your heart isn't pumping enough blood to your brain.\n\n"
            "# When to See a Doctor\n"
            "Generally, see your doctor if you experience any unexplained, recurrent, or severe dizziness or vertigo."
        )
    }

    # 1. Ingestion Phase: Chunking + Dual Vectors + Metadata -> Qdrant
    index.index([sample_doc])

    collection_name = settings.qdrant_collection
    count = index.client.count(collection_name).count
    assert count >= 2  # Multi-chunk document created multiple points

    # 2. Retrieval Phase: Hybrid Search (BGE Dense + BM25 Sparse with RRF)
    candidates = index.search_candidates("What causes spinning vertigo?", "health", limit=4)
    assert len(candidates) > 0

    # 3. Adjacent Chunk Expansion: Context Window Stitching
    expanded = index.expand_adjacent_chunks(candidates)
    assert len(expanded) > 0
    first_expanded = expanded[0]
    assert "expanded_text" in first_expanded
    assert "metadata" in first_expanded

    # 4. Cross-Encoder Reranking (BAAI/bge-reranker-base)
    reranked = index.rerank_passages("What causes spinning vertigo?", expanded, top_k=2)
    assert len(reranked) > 0
    assert "rerank_score" in reranked[0]

    # 5. End-to-End search_hybrid_pipeline
    pipeline_results = index.search_hybrid_pipeline("What causes spinning vertigo?", "health", top_k=2)
    assert len(pipeline_results) > 0
    assert pipeline_results[0]['source_id'] == 'doc_dizziness_101'

    # 6. Backward compatible source_id search
    source_ids = index.search("What causes spinning vertigo?", "health")
    assert 'doc_dizziness_101' in source_ids

    index.client.close()
