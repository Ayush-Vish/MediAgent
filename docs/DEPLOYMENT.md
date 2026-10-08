# Free demo deployment

## 1. Supabase: persistence and staff accounts

Create a free Supabase project. Get its **Session pooler** PostgreSQL connection string and use:

```text
DATABASE_URL=postgresql+psycopg://postgres.PROJECT:ENCODED_PASSWORD@POOLER_HOST:5432/postgres?sslmode=require
SUPABASE_URL=https://PROJECT.supabase.co
SUPABASE_ANON_KEY=YOUR_PUBLIC_ANON_KEY
```

URL-encode special characters in the database password. Keep the database connection string server-side. Use the session pooler, not transaction pooling, for this initial setup. The API creates its own `mediagent_records` table and enables row-level security/revokes browser-role access at initialization. It connects as the table-owning server role. Never put a service-role key or database password in frontend files.

Create staff users in Supabase Auth. Assign `app_metadata.role = "mediagent_staff"` and `app_metadata.hospital_id = "cmc-vellore"` with the privileged Admin API or SQL editor. [supabase.sql](supabase.sql) contains a commented SQL example and access-hardening statements. User-editable `user_metadata` is never used for staff authorization. Staff log in at `/staff`; access tokens stay in page memory, and expired sessions require another sign-in.

## 2. Render: deploy the web app

Push **the contents of MediAgent** to a repository you control, then create a Render Blueprint from `render.yaml`. If using a monorepo, set its root directory to `MediAgent` instead.

Set the database and Supabase variables above, `ENVIRONMENT=cloud`, and `PUBLIC_URL=https://YOUR-SERVICE.onrender.com`. The URL must exactly match the browser origin. Use the Free plan and leave `HYBRID_ENABLED=false` and `WHATSAPP_ENABLED=false`. No paid fallback is automatically activated.

The service runs a single worker, serves the web assets itself and checks `/healthz`. After deploying, verify:

1. An appointment question returns a CMC source link.
2. An unsupported price/availability question abstains.
3. Staff login rejects an ordinary Supabase user.
4. A demo review and reply are visible only to the requesting session.
5. A service restart preserves that sanitized conversation.

Render Free sleeps after inactivity and uses an ephemeral filesystem. Persisted records therefore live in Supabase; never override cloud mode with a local SQLite path. Quotas and free-tier terms should be checked in the providers' dashboards. Public knowledge is seeded automatically only for the CMC deployment.

## 3. Optional AI provider strategy

Set `AI_PROVIDER=gemini`, `GEMINI_API_KEY` and `GEMINI_MODEL` to use Gemini. To use OpenRouter, set `AI_PROVIDER=openrouter`, `OPENROUTER_API_KEY` and `OPENROUTER_MODEL`; `AI_BASE_URL` defaults to `https://openrouter.ai/api/v1`. For OpenAI, Groq, Together or a self-hosted OpenAI-compatible service, set `AI_PROVIDER=openai_compatible`, `AI_API_KEY`, `AI_MODEL`, and its HTTPS `AI_BASE_URL`. Use `AI_PROVIDER=local` to disable external AI selection. `AI_DAILY_LIMIT` or `GEMINI_DAILY_LIMIT` caps application calls; provider limits may be lower. No billed fallback is configured. A timeout, quota failure or malformed model result produces the local cited answer.

All providers receive fixed general questions and public excerpts only. They select evidence IDs, and the app constructs the answer from those excerpts. This deliberately avoids generated medical claims. Provider secrets are read server-side and are never included in the evidence prompt. Review each provider's data-processing terms before connecting a real hospital. Do not turn raw conversational prompts or tracing exports on without revisiting that architecture.

## 4. Telegram

Create your own bot through BotFather. Set:

```text
TELEGRAM_BOT_TOKEN=YOUR_BOT_TOKEN
TELEGRAM_WEBHOOK_SECRET=A_LONG_RANDOM_SECRET
CHANNEL_HASH_SECRET=A_DIFFERENT_RANDOM_SECRET_AT_LEAST_32_CHARACTERS
```

Use the same settings locally to run the explicit registration command after deployment:

```powershell
.\.venv\Scripts\python.exe -m scripts.telegram_webhook
```

Only private text messages are processed. `/start` introduces the demo, `/review` requests a demo review, `/status` reads saved staff replies, and `/forget` clears conversation records. Staff replies are retrieved by the user rather than pushed unsolicited. Bot registration and real outbound messages are not performed by setup or tests.

## 5. Optional Qdrant hybrid search

Create a Qdrant free cluster and set `QDRANT_URL` plus `QDRANT_API_KEY`. Install `requirements-rag.txt` on the machine doing indexing. Connect that machine to the same PostgreSQL database, approve sources in the staff UI, then run:

```powershell
.\.venv\Scripts\python.exe -m scripts.index_vectors
```

The first run downloads a MiniLM ONNX model and BM25 assets. Indexing upserts stable point IDs; approval, category, hospital scope and freshness remain enforced by the database at retrieval time. Stale vector entries cannot expose withdrawn text because their IDs must match active database candidates.

For serving hybrid queries, install `requirements-rag.txt` on the host and set `HYBRID_ENABLED=true`. Render users must change the build command accordingly. Embedding models load lazily; **512 MB may be insufficient**, especially with `RERANK_ENABLED=true`. Keep the default lexical profile for the lowest-resource free deployment. Optional reranking uses the available FastEmbed MiniLM cross-encoder, not the old PyTorch vision stack.

## 6. WhatsApp — disabled until explicitly configured

Use an official Meta WhatsApp Cloud API account and your own business phone number. Confirm Meta's current pricing and account requirements. Set these only when ready:

```text
WHATSAPP_ENABLED=true
WHATSAPP_ACCESS_TOKEN=YOUR_TOKEN
WHATSAPP_PHONE_ID=YOUR_PHONE_NUMBER_ID
WHATSAPP_APP_SECRET=YOUR_META_APP_SECRET
WHATSAPP_VERIFY_TOKEN=YOUR_RANDOM_VERIFICATION_TOKEN
WHATSAPP_GRAPH_VERSION=YOUR_SUPPORTED_GRAPH_API_VERSION
```

Configure Meta's callback as `https://YOUR-SERVICE.onrender.com/webhooks/whatsapp`. Verification uses the verify token; POST delivery uses the app-secret HMAC signature and checks the configured phone ID. The adapter replies to incoming text messages only and does not schedule outbound templates or reminders. Provider rejection leaves the reply pending for a webhook retry. Pricing is not assumed to be free.

## Known verification boundaries

Automated tests exercise local SQLite, mocked message/model providers and the actual Qdrant local engine with synthetic embeddings. They do not prove Supabase connectivity/RLS in your account, real Telegram/WhatsApp delivery, Qdrant cloud credentials, embedding quality, free-host memory fit or Docling OCR quality. Run those checks with the appropriate public/test data before inviting users. Clinical use is outside this demo's validated scope.

References: [Render free services](https://render.com/docs/free), [Supabase plans](https://supabase.com/pricing), [Qdrant plans](https://qdrant.tech/pricing/), [Gemini terms](https://ai.google.dev/gemini-api/terms), [Telegram Bot API](https://core.telegram.org/bots/api), [WhatsApp pricing](https://business.whatsapp.com/products/platform-pricing).
