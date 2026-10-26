# MediAgent

A hospital information chatbot with a web interface, demo staff workspace, Telegram adapter and disabled-by-default WhatsApp adapter. The initial knowledge base uses **CMC Vellore's official public information** and NHS general health education. This is an independent demo, not a CMC service.

## Run locally

Python 3.12 is recommended. From this folder in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m scripts.setup_local
.\.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

Open **http://localhost:8000**. Use that hostname because it matches the default `PUBLIC_URL` origin check. The app starts without API keys, GPU, model downloads or cloud accounts. SQLite stores local state in `runtime/mediagent.db`.

For `/staff`, copy the generated `ADMIN_TOKEN` from your local `.env` into the staff sign-in form. Setup preserves an existing `.env`; if it has an empty token, set a long random value and restart. There is no default password. This local token is disabled in cloud mode; cloud staff use Supabase Auth.

## Included behavior

- Responsive patient chat, source links, a new-conversation/delete action and demo staff replies.
- LangGraph routes hospital questions, health education, urgent warning phrases and unsupported requests.
- Local BM25-style retrieval with query expansion and evidence excerpts; unknown, stale, unapproved and explicitly conflicting sources are excluded.
- Optional Qdrant dense/BM25 fusion with CPU ONNX BGE-small embeddings and an optional memory-bounded MiniLM cross-encoder reranker.
- Web-search agent that complements local retrieval. An AI provider rewrites the question into a short search query, DDGS discovers pages from NHS, Mayo Clinic, and MedlinePlus, and MediAgent accepts content only from configured HTTPS medical domains. Web evidence is ephemeral, citation-validated, and labelled `limited`; it is never added to the approved corpus.
- Optional provider Strategy (**Gemini, OpenRouter, OpenAI-compatible endpoints, or local**) for grounded answer drafting. The external model receives a redacted query, a bounded recent-message context, and approved source excerpts, never stored patient records. Citation IDs are validated and unsafe, uncited, invalid, failed, or quota-exhausted model responses fall back to local excerpts.
- Source review and withdrawal; changed content returns to pending approval. Ingestion and Qdrant writes use stable IDs.
- Durable sanitized turn snapshots and topic continuity across restarts. Raw graph input is kept in memory only, and external LangSmith tracing is disabled. This is turn-level persistence, not mid-node LangGraph replay.
- Verified channel webhooks, deduplication, retryable delivery and channel-specific session identities. Telegram accepts private text chats only.

For another hospital, set `HOSPITAL_ID`, `HOSPITAL_NAME` and `HOSPITAL_PORTAL_URL`, then import its approved public sources. CMC demo sources are not seeded for other hospital IDs. Each deployment supports one hospital.

The seed corpus covers appointment guidance, preparation, contacts, campus information, booking changes, insurance, general cough education, and medicine basics such as the difference between paracetamol and the Dolo brand. It does not contain live doctor availability, prices, parking or visiting hours. It does not book appointments, prescribe, diagnose, accept patient uploads or analyze medical images. Medical routing is a conservative demo rule set, not a clinically validated triage system. Add more approved public sources through the staff workspace or offline ingestion to extend it to other hospital and general-health topics.

## Public sources

The short editorial summaries in `data/sources.json` were checked on **7 October 2026**:

- [CMC patient portal](https://www.cmcvellore.ac.in/patient-portal/)
- [CMC patient FAQs](https://www.cmcvellore.edu.in/faqs/)
- [CMC insurance / ACCESS](https://www.cmcvellore.ac.in/insurance-access/)
- [NHS cough education](https://www.nhs.uk/symptoms/cough/)

The older 2021 CMC guide is deliberately not used for current operational answers. Bundled summaries are approved for this independent demo, not medically or administratively approved by CMC. Sources expire from retrieval after 90 days by default. Retrieval scores are ranking heuristics, not probabilities of clinical correctness.

Fetch updated CMC pages locally:

```powershell
.\.venv\Scripts\python.exe -m scripts.ingest --fetch-cmc
```

The command saves HTML and provenance metadata under ignored `data/raw/`, imports page chunks as **pending**, and retires removed chunks on reimport. Open `/staff` to inspect the public text and approve it. Review conflicting policies explicitly; conflict detection is a staff responsibility. Preserve a source's title, URL and page when editing it so its stable identity is retained.

Import the current MedlinePlus public health-topic dataset (about 2,000 English topics, split into searchable chunks):

```powershell
.\.venv\Scripts\python.exe -m scripts.ingest --fetch-medlineplus
```

This downloads the official compressed XML from MedlinePlus, keeps the topic URL and alternate names, and writes the chunks to the local database as **pending**. Staff must review and approve them before retrieval can use them. This dataset is health education, not a complete medicine formulary or a source for personal diagnosis, dosing, or prescribing. Drug labels and hospital-approved medication protocols should be ingested separately after licensing and clinical review.

Import the NHS medicine starter corpus:

```sh
python -m scripts.ingest --fetch-medicines
# Review and approve imported chunks in /staff, then:
python -m scripts.index_vectors
```

This repeatable pipeline imports paracetamol, ibuprofen, cetirizine, amoxicillin,
metformin, omeprazole, atorvastatin, and salbutamol inhaler information, including
linked NHS patient-information subpages. It logs each URL and chunk count, keeps
raw HTML with SHA-256 provenance under `data/raw/`, and writes **pending** records
into `DATABASE_URL`. Reimports keep stable IDs, require renewed approval for
changed content, and retire removed chunks. A failed or empty page does not retire
its previous chunks; the command exits with an error and can be rerun.

Use the **same persistent database** as the API for production imports. After
staff approval, indexing uses `QDRANT_URL`, `QDRANT_API_KEY`, and
`QDRANT_COLLECTION` (default `mediagent`). Import alone does not publish vectors.
Both commands are available in the Docker image; indexing hosts need
`requirements-rag.txt`. Rerun ingestion when refreshing the data, review changes,
and then rerun indexing before the 90-day source expiry. Avoid overlapping runs.

Content attribution: [NHS website](https://www.nhs.uk/medicines/), retrieved with
its page URL and fetch date. Review applicable [NHS content terms](https://www.nhs.uk/our-policies/terms-and-conditions/)
before redistribution. This is a bounded education corpus, not a prescribing
formulary; existing personal dosing and treatment guards still apply.

Download official openFDA bulk medicine reference data separately:

```sh
python -m scripts.download_openfda --scope medicine
# Rerun the same command to resume interrupted transfers and verify completed ZIPs.
# Inspect the full export size before choosing a sufficiently large destination:
python -m scripts.download_openfda --scope all --plan
python -m scripts.download_openfda --scope all --output /path/to/large-disk/openfda
```

The medicine scope includes drug labels, NDC, Drugs@FDA, Orange Book,
enforcement recalls, and shortages. It excludes adverse-event reports. Downloads
stay compressed under ignored `data/raw/openfda/`, grouped by dataset and export
date, with the official `download.json` manifest and SHA-256 receipts. The manifest
is pinned until `--refresh` is supplied. The downloader reserves 1 GiB of disk
space, checks ZIP CRCs, and resumes partial transfers when the server supports
ETag-validated range requests; otherwise it restarts that archive. Quarterly export
paths remain distinct. Completed files are checked against their recorded hashes.

These are raw regulatory datasets, **not approved chat sources**. Downloading does
not import them into the application database or Qdrant. Labels include prescribing
information and require an explicit transformation and review step before use in
patient-facing answers. Keep [openFDA attribution, license, and terms](https://open.fda.gov/license/)
with downstream datasets. No automatic extraction or embedding job runs on Render.

Chunk and ingest the downloaded medicine reference archives into local SQLite:

```sh
python -m scripts.ingest_openfda
```

This streams `results` from verified ZIPs without extracting whole files, keeps
label sections separate, and uses the existing recursive chunker with 180-word
chunks and 30-word overlap where layout permits. Every chunk includes record
identity, medicine names when available, and the export date. Source links point
to the official archive with a record-number fragment; the saved archive receipt
and database checkpoint retain the full SHA-256 digest. Label HTML tables are
converted to readable text and retained alongside their other sections.

The command imports all six downloaded medicine reference datasets into
`DATABASE_URL` (local SQLite only; default `runtime/mediagent.db`). Sources are
permanent and **pending**, so ingestion does not publish answers or vectors.
Archive checkpoints skip finished work; interrupted archives can be rerun with
stable source IDs. Different archive checksums represent separate immutable
snapshots, not automatic updates to approved clinical sources. Large corpora can
produce millions of chunks; retain enough disk space for the database. Progress
logs include record and chunk counts. Retrieval filters approved sources in the
database through a partial index before loading text into memory. Run only one
import process at a time.

Upload a capacity-bounded medicine corpus to Qdrant Cloud after recursive chunking:

```sh
python -m scripts.upload_openfda --env-file MediAgent.env
```

Set `QDRANT_URL`, `QDRANT_API_KEY`, and `QDRANT_COLLECTION` in that local credential
file. `*.env` files are excluded from Git and Docker build contexts. The upload
uses at most **50,000 points** for the current 1 GB RAM / 5 GB disk cluster. It
selects representative human medicine labels, deduplicates generic-name, brand,
and route combinations, and keeps indications, warnings, interactions, adverse
reactions, pregnancy, and patient-information sections. This is a bounded subset,
not the entire 11-million-chunk local corpus.

The selected text uses the existing recursive chunks and source IDs, without a
second split. Dense BGE and sparse BM25 embeddings are generated locally in small
batches, then upserted into the configured cloud collection. Regulatory IDs and
export dates stay in payloads rather than consuming the medical embedding input.
Dense vectors, HNSW, sparse indexes, and payloads use disk-backed storage. The
script never deletes or recreates the existing collection. It saves a checkpoint
only after Qdrant acknowledges a batch, so rerunning the same command resumes
safely with stable point IDs.

**Uploading is not clinical approval.** Pending points carry `approved=false`
and are excluded from patient-facing vector searches. Legacy points without that
field remain eligible, with database approval still authoritative. Patient-facing
use also requires the matching source records in the serving database; a local
SQLite import does not automatically populate production PostgreSQL.

For a public PDF, install the heavier offline parser separately:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-ingest.txt
.\.venv\Scripts\python.exe -m scripts.ingest --file public-guide.pdf --source-url https://YOUR-HOSPITAL/public-guide.pdf --title "Public patient guide" --checked-at 2026-10-07
```

Docling extracts text with page provenance, including OCR where supported. Review tables, OCR and image-derived information before approval. This is public document extraction, not diagnostic image interpretation. Do not run Docling on the free web-service instance or ingest patient records.

## Deploy and connect channels

See [deployment instructions](docs/DEPLOYMENT.md). `render.yaml` and `Dockerfile` are included. Cloud mode requires persistent PostgreSQL and Supabase staff authentication rather than silently using an ephemeral disk.

**A zero-cost demo is the target, not guaranteed free hospital production.** Render free instances sleep, provider quotas change, and WhatsApp can incur charges. Default hosting uses local retrieval to avoid fitting embedding/reranking models into a small free instance. Enable hybrid retrieval only after measuring memory in the chosen hosting plan.

## API

Interactive schemas are available at `/docs`.

| Endpoint | Purpose |
| --- | --- |
| `POST /api/session` | Create or restore an HttpOnly-cookie conversation |
| `POST /api/chat` | `{message, request_id}` → `{text, route, sources, evidence, mode}` |
| `GET /api/history` | Own chat history and staff replies |
| `DELETE /api/session` | Delete own conversation and review records |
| `POST /api/reviews` | Request a demo handoff for the current conversation |
| `GET /api/staff/reviews` | Authenticated review queue |
| `POST /api/staff/reviews/{id}/reply` | Save a general-information reply |
| `GET/POST /api/staff/sources` | List or stage public sources |
| `PATCH /api/staff/sources/{id}` | Approve or withdraw a source |
| `POST /webhooks/telegram` | Secret-verified, consent-gated Telegram updates |
| `GET/POST /webhooks/whatsapp` | Meta verification and signed updates; disabled by default |
| `GET /healthz` | App/database health |

## Privacy, retention and operational limits

Anonymous web chats retain complete questions and answers in browser local storage; the backend stores only a fixed public topic or privacy placeholder. On sign-in, browser history is copied into the authenticated patient conversation and cleared only after every turn is saved. Interrupted imports retry without duplicating messages and remain bound to the original account. Imported browser answers are untrusted text, so source claims and staff roles are not imported. Signed-in messages, AI answers, and doctor messages persist in the database until the conversation is cleared. Signing in again restores the account conversation, including on another device; another patient receives a separate conversation. The session cookie contains only an identifier, not chat text. Symptom events for signed-in patients are stored permanently by the existing timeline feature. Telegram/WhatsApp themselves still receive messages typed into those platforms. Regex filters are not comprehensive de-identification.

Anonymous server sessions and review/delivery records expire after 24 hours without an update. Signed-in sessions and patient messages do not expire under `RETENTION_HOURS`. Anonymous browser history remains until cleared or successfully transferred on sign-in; clearing browser data also removes it. Cleanup runs on startup and every five minutes while awake. Expired records are immediately inaccessible through the store, even before cleanup. At most 50 server turn snapshots per session are retained; signed-in message history and anonymous browser history retain the full conversation. Public knowledge sources persist until withdrawn or revised. Staff should never put personal data in public source text.

### Patient management and doctor chat

The Next.js patient chat uses `POST /api/chat/stream` with Server-Sent Events. Gemini uses `streamGenerateContent?alt=sse`; OpenRouter, Groq and OpenAI-compatible providers use streamed chat completions. The browser displays provisional text as it arrives. A `done` event replaces the draft with the complete answer after the existing citation/safety checks and persistence; an `error` event clears the draft and keeps the question available for retry. Key rotation, provider fallback, the latest-three-message context and RAG evidence still apply. Only final answers are saved. Guarded/local answers and idempotent retries return a complete `done` event without artificial token animation. SSE connections include keepalives and disable proxy buffering; errors after streaming starts arrive as an SSE event with their status/code rather than changing the HTTP status. `/api/chat` remains available for Telegram and other JSON clients.

Each AI chat answer receives the current query, retrieved RAG evidence when available, and the latest **three individual messages** from the same conversation in chronological order. The current query is excluded from that history. Signed-in context includes doctor messages and survives restarts while those records remain retained. Anonymous context stays in bounded process memory and resets when the backend restarts or the chat is cleared. History is untrusted context, not a source of verified citations. Logs include `chat.context message_count=… limit=3`, without copying context text into JSON logs.

Use the Next.js frontend at `/staff`. Local access requires the actual `ADMIN_TOKEN`; cloud access requires a Supabase account with the configured hospital's staff role. The public configuration does not expose the staff token. In **Patients & conversations**, select a patient, load their records, choose a conversation, and send a doctor message. Patient and staff views poll for new doctor/patient messages every five seconds. Only new signed-in conversations contain a full transcript; older privacy placeholders cannot be recovered.

Symptom summaries are absent from the patient interface. Staff can inspect the recorded timeline and explicitly click **Generate AI timeline summary**. This is a separate provider-pool call with the same sliding-window limits and failover as chat. It uses up to the latest 100 events in the selected interval, omits profile/contact fields, and labels the result as an AI draft for clinician review. If no provider is available, the dashboard displays the API's 503 error. The summary is not sent to the patient.

### Connect Telegram

Create a bot with Telegram's `@BotFather`, then configure these server-side values:

```dotenv
TELEGRAM_BOT_TOKEN=<your bot token>
TELEGRAM_WEBHOOK_SECRET=<random secret>
CHANNEL_HASH_SECRET=<random secret of at least 32 characters>
PUBLIC_URL=https://<public API domain>
```

Deploy the FastAPI API at that HTTPS address, restart it, and register the existing webhook adapter:

```powershell
.\.venv\Scripts\python.exe -m scripts.telegram_webhook
```

Telegram sends private text updates to `/webhooks/telegram`; the API validates Telegram's secret header and deduplicates updates. `/start` or `/privacy` shows the retention notice, and `/accept` creates a pseudonymous Telegram patient visible in the authenticated staff dashboard. Subsequent messages run through the same RAG/LLM pipeline and are retained with AI replies so hospital staff can review them. A doctor reply from **Patients & conversations** is delivered back into the same Telegram private chat through an idempotent delivery record. The adapter also supports `/review`, `/status`, and `/forget`; `/forget` removes the retained conversation, alias, channel binding, and symptom records. Webhook registration is an explicit owner operation and has not been performed automatically.

`CHANNEL_HASH_SECRET` also derives the AES-GCM key used to protect Telegram delivery addresses. Back it up securely: rotating or losing it disconnects existing Telegram conversations. The database still contains medical chat content after consent, so use encrypted database storage, restricted backups, least-privilege staff accounts, and an appropriate retention policy. This demo does not by itself establish HIPAA, DPDP, or other regulatory compliance.

Run **one API worker / one replica**. Locks and quota counters are designed for that deployment. Webhook delivery is at-least-once at the provider boundary: a crash after a provider accepts a message but before the sent marker is saved can produce a duplicate. Do not claim exactly-once delivery. Normal duplicate updates and retry-after-failure are covered by tests.

New-session creation and message traffic are rate limited. Free-tier cold starts can trigger provider retries; no keep-awake workarounds are included. Real hospital rollout requires hospital-approved content and staffing, appropriate data-processing arrangements, operational monitoring and clinical validation of any medical features.

## API key pools

Add comma-separated keys to `.env`, then restart the server:

```dotenv
AI_PROVIDER=openrouter
OPENROUTER_API_KEYS=key-one,key-two,key-three
# Alternatively GEMINI_API_KEYS or AI_API_KEYS for a compatible endpoint.
AI_POOL_RPM=60
AI_POOL_CONCURRENCY=2
AI_POOL_MAX_ATTEMPTS=3
AI_POOL_SHARED_LIMITS=false
```

Keep your existing model and base URL settings. Plural key settings override the
corresponding singular setting; singular settings also accept comma-separated
keys. Blank entries and duplicates are removed. The pool selects the key with
the most unused request capacity and reserves a request before sending it.
All model calls share the pool, including evidence selection and answer drafting.

HTTP 429 triggers a cooldown using `Retry-After` (seconds or HTTP date), with
1/2/4/.../60-second exponential backoff when absent. Each key is tried at most
once per call, up to `AI_POOL_MAX_ATTEMPTS`. Invalid credentials (401/403) are
disabled until restart. Other HTTP/network errors do not rotate. Exhaustion
returns immediately to the graph's existing fallback rather than sleeping.

For keys sharing a provider account/project quota, set `AI_POOL_SHARED_LIMITS=true`:
request/concurrency limits and 429 cooldowns apply across the pool. Rotation does
not create additional account quota. These are local request limits, not token
limits; provider responses remain authoritative. The daily application quota
still applies. State is held in memory in one worker and resets on restart;
multiple workers require a shared limiter. Each provider/endpoint has a separate
pool. To automatically switch providers, use `AI_PROVIDER=pool` and configure
each provider's keys AND model (`OPENROUTER_API_KEYS` / `OPENROUTER_MODEL`,
`GEMINI_API_KEYS` / `GEMINI_MODEL`, or `AI_API_KEYS` / `AI_MODEL` / `AI_BASE_URL`).
Pool mode tries OpenRouter, Gemini, Groq, then the compatible endpoint. Missing key/model
pairs are skipped. The daily app quota in pool mode is shared across providers.
All-provider exhaustion returns HTTP 503 with `code: LLM_UNAVAILABLE` and a safe
`error` message that the chat UI displays; failed turns are not saved as answers.
Input classification and final-output review use structured LLM guardrail calls,
so at least one configured AI provider is required for every chat turn. Provider
output is buffered and is shown to the user only after the output guardrail allows
or safely rewrites the complete answer.

Application code can use the provider-independent object directly:

```python
from app.config import Settings
from app.llm_pool import LLMPool

llm = LLMPool.from_settings(Settings())
answer = llm.generate("What is general cough education?")
# For grounded generation:
answer = llm.generate(query, category="health", sources=retrieved_sources)
```

Sliding windows, key reservations, and cooldowns stay inside the provider pools.
The direct interface applies pool limits; the chatbot graph adds its daily quota
and response validation. State is process-local. New credentials require a restart.

## Verification

Local hybrid retrieval can use `QDRANT_PATH=runtime/qdrant` with
`HYBRID_ENABLED=true` instead of a remote `QDRANT_URL`. Install
`requirements-rag.txt` and run `python -m scripts.index_vectors` before querying.
Stop the API before reindexing: local Qdrant permits only one process per storage
directory. The index persists across restarts; use one API worker. A remote
Qdrant server remains the option for concurrent processes or cloud hosting.

Web search is enabled by default and runs alongside local retrieval. Configure an
AI provider for LLM query rewriting; a sanitized original query is used if the
provider is unavailable. The default health allowlist is
`nhs.uk,mayoclinic.org,medlineplus.gov`; change
`WEB_SEARCH_TRUSTED_DOMAINS` to a comma-separated list if your review policy
requires a different set. Hospital queries are restricted to the hostname in
`HOSPITAL_PORTAL_URL` (or the bundled CMC portal). If rewriting or search fails,
local retrieval and the normal abstention/general-health behavior remain available.

Set `FIRECRAWL_API_KEY` and use `WEB_SEARCH_BACKEND=firecrawl` to search through
Firecrawl's `/v2/search` API and reuse the returned page Markdown directly. With
`WEB_SEARCH_BACKEND=auto`, Firecrawl is preferred whenever its key is configured;
otherwise the DuckDuckGo client remains the fallback. API credentials are read only
from environment variables and are never written to logs or committed files.

Groq uses its [OpenAI-compatible API](https://console.groq.com/docs/openai).
Add `GROQ_API_KEYS=key1,key2` (or `GROQ_API_KEY` for one key) and
`GROQ_MODEL=your-model-id` to `.env`, then restart. Keep `AI_PROVIDER=pool`
for automatic fallback, or use `AI_PROVIDER=groq` for Groq only. Groq has
its own key pool and uses the existing rate-limit, cooldown and logging settings.

Pipeline console logs include timestamps, request IDs, timings, and provider status.
Set `LOG_SHOW_QUERY=true` and `LOG_SHOW_CHUNKS=true` to display user queries and
retrieved source text on stderr in any environment, including cloud deployments.
Both settings default to false; `.env.example` and `render.yaml` explicitly enable
them. Cloud log collectors will retain this console output, which can include
patient information. Set either flag to false to hide that content. Rich uses
bordered panels without terminal colors when output is redirected; plain text
output also supports queries and chunks when Rich is unavailable.
Queries and chunk text remain excluded from structured JSON file logs.
Structured JSON logs are written to `runtime/logs/mediagent.jsonl`.
Files rotate at 5 MB with three backups. Set `LOG_FILE=` for console only.
Each HTTP response includes `X-Request-ID`; use it to correlate JSON events for
the HTTP request, chat, routing, retrieval, vector embedding/search, adjacent
expansion, reranking, provider/key attempts, cooldowns, validation, and answer.
Events include durations, counts, statuses and exception types, never chat text,
prompts, source text, API keys, or exception bodies. A completed span means the
step returned; use `http.response.status` and result events for its outcome.
Vector events appear only when hybrid retrieval actually runs. Existing library
and Uvicorn logs remain separate from these structured pipeline logs.

```powershell
Get-Content runtime/logs/mediagent.jsonl -Wait -Tail 30
```

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check .
node --check web/chat.js
node --check web/staff.js
```

Tests exercise citations, guarded routes, session isolation, turn persistence, review access, staff roles, source approval/freshness, provider fallback, idempotent ingestion, signature verification and webhook delivery retries. External providers are mocked; live credentials are not required or used. Cloud deployment, live messaging and OCR/model downloads need separate integration verification in the owner's accounts.
