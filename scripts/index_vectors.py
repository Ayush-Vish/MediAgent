"""Run offline after staff approval to refresh Qdrant embeddings."""
from app.config import Settings
from app.knowledge import Knowledge
from app.store import Store
from app.vector import HybridIndex


def main():
    settings = Settings()
    if not (settings.qdrant_url or settings.qdrant_path):
        raise SystemExit('Set QDRANT_URL or QDRANT_PATH before indexing.')
    knowledge = Knowledge(Store(settings.database_url), settings)
    knowledge.seed()
    sources = knowledge.active('hospital') + knowledge.active('health')
    index = HybridIndex(settings)
    try:
        index.index(sources)
    finally:
        index.client.close()
    print(f'Indexed {len(sources)} approved sources with stable IDs.')


if __name__ == '__main__':
    main()
