# Verification — 7 October 2026

- 44 automated tests passed on Python 3.12 in the project's isolated environment.
- Ruff, all three frontend JavaScript syntax checks, and `pip check` passed.
- A running Uvicorn instance served the patient page, staff page, static assets and database health endpoint successfully.
- Live HTTP checks verified appointment citation ranking, staff-token authentication, saving a review reply, patient receipt of that reply, isolation from another session, and deletion of the temporary test conversations.
- The local knowledge database contains 8 approved demo summaries and 51 imported CMC chunks pending staff review. Raw public pages and provenance are in `data/raw/` and excluded from version control.
- The Qdrant integration test exercises its actual in-memory engine with synthetic dense/sparse vectors: stable upserts, fusion queries and hospital/category filters. Qdrant warns that payload indexes have no effect in local mode; filtering still passes.

Not verified: browser visual rendering (no browser automation connection available), external Supabase/PostgreSQL deployment, real Telegram/WhatsApp delivery, downloaded embedding/reranker behavior, Docling OCR output or free-host memory fit. Those integrations require their respective setup and are not represented as deployed or clinically validated.

The default demo uses local evidence retrieval. Optional cloud model selection and vector search are disabled until configured. No real channel messages were sent during implementation.
