import asyncio
import hashlib
import hmac
import json
import logging
from contextlib import asynccontextmanager, suppress
from urllib.parse import urlsplit

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from app.llm_pool import LLMUnavailable
from app.observability import configure_logging, event, request_id, span
from uuid import uuid4
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from app.auth import authenticate_user, get_user_from_token, logout_user, register_user
from app.channels import Channels
from app.config import ROOT, Settings
from app.graph import ChatGraph
from app.knowledge import Knowledge
from app.safety import reply_allowed
from app.schemas import (Answer, AuthResponse, ChatRequest, EmbedRequest,
                         PatientSummaryResponse, RagSearchRequest, RerankRequest,
                         ReviewReply, SourceDecision, SourceInput, UserLogin,
                         UserRegister)
from app.service import ChatService
from app.schemas import DoctorMessage, TimelineSummaryRequest
from app.store import Store
from app.vector import HybridIndex

log = logging.getLogger(__name__)


def create_app(settings=None):
    settings = settings or Settings()
    configure_logging(settings.log_file, show_chunks=settings.log_show_chunks and settings.environment == 'local',
                      show_query=settings.log_show_query and settings.environment == 'local')
    store = Store(settings.database_url, settings.retention_hours)
    knowledge = Knowledge(store, settings)
    knowledge.seed()
    service = ChatService(store, ChatGraph(knowledge, store, settings))
    channels = Channels(settings, store, service)

    async def cleanup():
        while True:
            await run_in_threadpool(store.prune)
            await asyncio.sleep(300)

    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(cleanup())
        yield
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        if app.state.stream_tasks:
            await asyncio.gather(*app.state.stream_tasks, return_exceptions=True)
        store.engine.dispose()
        if knowledge._hybrid is not None:
            knowledge._hybrid.client.close()

    app = FastAPI(title='MediAgent', version='1.0.0', lifespan=lifespan)
    app.state.store, app.state.knowledge = store, knowledge
    app.state.service, app.state.channels = service, channels
    app.state.stream_tasks = set()

    @app.middleware('http')
    async def boundaries(request, call_next):
        origin = request.headers.get('origin')
        expected = urlsplit(settings.public_url)
        allowed_origins = {f'{expected.scheme}://{expected.netloc}'}
        if getattr(settings, 'cors_origins', ''):
            for item in settings.cors_origins.split(','):
                item = item.strip().rstrip('/')
                if item:
                    allowed_origins.add(item)
        allowed_origins.add('https://frontend-eosin-eight-12.vercel.app')
        if settings.environment == 'local':
            allowed_origins.update({
                'http://localhost:8000',
                'http://127.0.0.1:8000',
                'http://localhost:3000',
                'http://127.0.0.1:3000',
                'http://localhost:3001',
                'http://127.0.0.1:3001',
            })

        def is_origin_allowed(orig: str | None) -> bool:
            if not orig:
                return False
            if orig in allowed_origins:
                return True
            try:
                parsed = urlsplit(orig)
                if parsed.scheme in {'http', 'https'} and parsed.hostname:
                    if parsed.hostname == 'frontend-eosin-eight-12.vercel.app':
                        return True
                    if parsed.hostname.endswith('.vercel.app') and ('frontend' in parsed.hostname or 'mediagent' in parsed.hostname):
                        return True
            except Exception:
                pass
            return False

        if request.method == 'OPTIONS':
            if origin and is_origin_allowed(origin):
                return Response(
                    status_code=204,
                    headers={
                        'Access-Control-Allow-Origin': origin,
                        'Access-Control-Allow-Credentials': 'true',
                        'Access-Control-Allow-Methods': 'GET, POST, PUT, PATCH, DELETE, OPTIONS',
                        'Access-Control-Allow-Headers': 'Content-Type, Authorization, X-Request-ID, X-CSRF-Token',
                    }
                )
            return Response(status_code=403)

        if request.method in {'POST', 'PATCH', 'DELETE'}:
            if origin and not is_origin_allowed(origin):
                return JSONResponse({'error': 'Origin not allowed'}, status_code=403)
            body = await request.body()
            if len(body) > 100_000:
                return JSONResponse({'error': 'Request too large'}, status_code=413)

        response = await call_next(request)
        if origin and is_origin_allowed(origin):
            response.headers['Access-Control-Allow-Origin'] = origin
            response.headers['Access-Control-Allow-Credentials'] = 'true'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        connect = "'self'" + (' ' + settings.supabase_url if settings.supabase_url else '')
        response.headers['Content-Security-Policy'] = f"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src {connect}; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        if request.url.path.startswith('/api') or request.url.path.startswith('/static') or request.url.path == '/':
            response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        return response

    @app.middleware('http')
    async def trace_request(request, call_next):
        token = request_id.set(str(uuid4()))
        try:
            with span('http.request', method=request.method):
                response = await call_next(request)
                response.headers['X-Request-ID'] = request_id.get()
                route = request.scope.get('route')
                route_path = getattr(route, 'path', None) or request.url.path
                event('http.response', status=response.status_code, route=route_path)
                return response
        finally:
            request_id.reset(token)

    @app.exception_handler(LLMUnavailable)
    async def llm_unavailable_handler(request, error):
        return JSONResponse({'error': str(error), 'code': 'LLM_UNAVAILABLE'}, status_code=503)

    @app.exception_handler(HTTPException)
    async def http_error(request, error):
        return JSONResponse({'error': str(error.detail)}, status_code=error.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        return JSONResponse({'error': 'Invalid request. Check the required fields and their limits.'}, status_code=422)

    @app.exception_handler(Exception)
    async def unexpected_error(request, error):
        log.error('Request failed: %s', type(error).__name__)
        return JSONResponse({'error': 'Service temporarily unavailable. Please retry.'}, status_code=503)

    def session(request: Request):
        token = request.cookies.get('mediagent_session')
        if not token or len(token) > 200:
            raise HTTPException(401, 'Start a new conversation.')
        return token

    def optional_user(request: Request):
        token = request.cookies.get('mediagent_auth')
        if not token:
            header = request.headers.get('authorization', '')
            if header.startswith('Bearer '):
                token = header[7:]
        return get_user_from_token(store, token)

    def current_user(request: Request):
        user = optional_user(request)
        if not user:
            raise HTTPException(401, 'Patient sign-in required.')
        return user

    def staff(request: Request):
        header = request.headers.get('authorization', '')
        if not header.startswith('Bearer '):
            raise HTTPException(401, 'Staff sign-in required.')
        token = header[7:]
        if settings.environment == 'local':
            if settings.admin_token and hmac.compare_digest(token, settings.admin_token):
                return 'local-demo-staff'
        if not settings.supabase_url or not settings.supabase_anon_key:
            raise HTTPException(401, 'Staff access is not configured.')
        try:
            result = httpx.get(settings.supabase_url + '/auth/v1/user', timeout=10,
                headers={'apikey': settings.supabase_anon_key, 'Authorization': header})
            result.raise_for_status()
            user = result.json()
        except (httpx.HTTPError, ValueError):
            raise HTTPException(401, 'Staff session expired or invalid.') from None
        claims = user.get('app_metadata', {})
        if claims.get('role') != 'mediagent_staff' or claims.get('hospital_id') != settings.hospital_id:
            raise HTTPException(403, 'This account is not authorised for this hospital.')
        return user['id']

    @app.get('/healthz')
    def health():
        store.get('healthcheck')
        return {'status': 'ok'}

    @app.get('/api/config')
    def config():
        return {'hospital': settings.hospital_name, 'demo': True,
                'portal_url': settings.portal_url,
                'ai_provider': settings.ai_provider,
                'supabase_url': settings.supabase_url, 'supabase_anon_key': settings.supabase_anon_key,
                'local_staff': settings.environment == 'local' and bool(settings.admin_token),
                'telegram_enabled': bool(settings.telegram_bot_token), 'whatsapp_enabled': settings.whatsapp_enabled,
                'retention_hours': settings.retention_hours}

    @app.post('/api/session')
    def new_session(request: Request, response: Response, user=Depends(optional_user)):
        host = request.client.host if request.client else 'unknown'
        key = hashlib.sha256(host.encode()).hexdigest()
        if not store.consume('quota:new:' + key, 30, 60):
            raise HTTPException(429, 'Please wait before creating another conversation.')
        token = request.cookies.get('mediagent_session')
        if token and len(token) <= 200:
            try:
                return service.history(token, user=user)
            except LookupError:
                pass
        token = service.create_session()
        cookie_samesite = 'none' if settings.environment == 'cloud' else 'lax'
        response.set_cookie('mediagent_session', token, httponly=True, samesite=cookie_samesite,
                            secure=settings.environment == 'cloud', max_age=settings.retention_hours * 3600)
        return {'turns': [], 'reviews': []}

    @app.post('/api/chat', response_model=Answer)
    def chat(body: ChatRequest, token=Depends(session), user=Depends(optional_user)):
        try:
            return service.chat(token, body.message, str(body.request_id), user=user)
        except LookupError as error:
            raise HTTPException(401, str(error)) from None
        except OverflowError as error:
            raise HTTPException(429, str(error)) from None

    @app.get('/api/history')
    def history(token=Depends(session), user=Depends(optional_user)):
        try:
            return service.history(token, user=user)
        except LookupError as error:
            raise HTTPException(401, str(error)) from None

    @app.post('/api/chat/stream')
    async def chat_stream(body: ChatRequest, token=Depends(session), user=Depends(optional_user)):
        from app.streaming import stream_sink
        loop = asyncio.get_running_loop()
        queue = asyncio.Queue()
        def emit(name, data):
            loop.call_soon_threadsafe(queue.put_nowait, (name, data))
        def work():
            sink_token = stream_sink.set(emit)
            try:
                with span('chat.stream'):
                    answer = service.chat(token, body.message, str(body.request_id), user=user)
                    emit('done', answer)
            except (LLMUnavailable, LookupError, OverflowError) as error:
                status = 503 if isinstance(error, LLMUnavailable) else 401 if isinstance(error, LookupError) else 429
                emit('error', {'error': str(error), 'status': status,
                               'code': 'LLM_UNAVAILABLE' if status == 503 else 'CHAT_ERROR'})
            except Exception:
                emit('error', {'error': 'Service temporarily unavailable. Please retry.', 'status': 503,
                               'code': 'CHAT_ERROR'})
            finally:
                stream_sink.reset(sink_token)
        async def events():
            task = asyncio.create_task(asyncio.to_thread(work))
            app.state.stream_tasks.add(task)
            task.add_done_callback(app.state.stream_tasks.discard)
            # Finish/persist in the worker even after disconnection, allowing an
            # idempotent retry to recover the completed answer without another call.
            yield 'event: status\ndata: {"text":"Finding sources and preparing your answer…"}\n\n'
            while True:
                try:
                    name, data = await asyncio.wait_for(queue.get(), timeout=10)
                except TimeoutError:
                    yield ': keepalive\n\n'
                    continue
                yield f'event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n'
                if name in {'done', 'error'}:
                    break
        return StreamingResponse(events(), media_type='text/event-stream', headers={
            'X-Accel-Buffering': 'no', 'Cache-Control': 'no-cache, no-store',
        })

    @app.delete('/api/session')
    def forget(response: Response, token=Depends(session)):
        service.forget(token)
        response.delete_cookie('mediagent_session')
        return {'ok': True}

    @app.post('/api/auth/register', response_model=AuthResponse)
    def register(body: UserRegister, response: Response):
        try:
            register_user(store, body.model_dump())
            auth_res = authenticate_user(store, body.email, body.password)
            if not auth_res:
                raise HTTPException(500, 'Authentication error after registration.')
            token, safe_user = auth_res
            cookie_samesite = 'none' if settings.environment == 'cloud' else 'lax'
            response.set_cookie('mediagent_auth', token, httponly=True, samesite=cookie_samesite,
                                secure=settings.environment == 'cloud', max_age=30 * 86400)
            return {'token': token, 'user': safe_user}
        except ValueError as error:
            raise HTTPException(400, str(error)) from None

    @app.post('/api/auth/login', response_model=AuthResponse)
    def login(body: UserLogin, response: Response):
        auth_res = authenticate_user(store, body.email, body.password)
        if not auth_res:
            raise HTTPException(401, 'Invalid email or password.')
        token, safe_user = auth_res
        cookie_samesite = 'none' if settings.environment == 'cloud' else 'lax'
        response.set_cookie('mediagent_auth', token, httponly=True, samesite=cookie_samesite,
                            secure=settings.environment == 'cloud', max_age=30 * 86400)
        return {'token': token, 'user': safe_user}

    @app.post('/api/auth/logout')
    def logout(request: Request, response: Response):
        token = request.cookies.get('mediagent_auth')
        if not token:
            header = request.headers.get('authorization', '')
            if header.startswith('Bearer '):
                token = header[7:]
        logout_user(store, token)
        response.delete_cookie('mediagent_auth')
        return {'ok': True}

    @app.get('/api/auth/me')
    def auth_me(user=Depends(optional_user)):
        return {'user': user}

    @app.get('/api/staff/patient-summary', response_model=PatientSummaryResponse)
    def staff_patient_summary(email: str | None = None, user_id: str | None = None, since_days: int = 30, actor=Depends(staff)):
        if not email and not user_id:
            raise HTTPException(400, 'Please provide patient email or user_id.')
        try:
            return service.get_patient_symptom_summary(user_id=user_id, email=email, since_days=since_days)
        except LookupError as error:
            raise HTTPException(404, str(error)) from None

    @app.get('/api/staff/patients')
    def staff_list_patients(actor=Depends(staff)):
        return {'patients': service.list_patients()}

    @app.get('/api/staff/patients/{user_id}/messages')
    def patient_messages(user_id: str, actor=Depends(staff)):
        try:
            return {'messages': service.patient_messages(user_id)}
        except LookupError as error:
            raise HTTPException(404, str(error)) from None

    @app.post('/api/staff/patients/{user_id}/messages')
    def doctor_message(user_id: str, body: DoctorMessage, actor=Depends(staff)):
        if not body.text.strip():
            raise HTTPException(400, 'Enter a message.')
        try:
            return service.doctor_message(user_id, body.conversation_id, body.text, actor, str(body.request_id))
        except LookupError as error:
            raise HTTPException(404, str(error)) from None

    @app.post('/api/staff/patients/{user_id}/summary')
    def generate_summary(user_id: str, body: TimelineSummaryRequest, actor=Depends(staff)):
        try:
            return service.generate_patient_summary(user_id, body.since_days)
        except LookupError as error:
            raise HTTPException(404, str(error)) from None

    @app.post('/api/reviews')
    def handoff(token=Depends(session)):
        try:
            review = service.handoff(token)
            return {'id': review['id'], 'status': review['status']}
        except LookupError as error:
            raise HTTPException(400, str(error)) from None

    @app.get('/api/staff/reviews')
    def reviews(actor=Depends(staff)):
        return [{k: v for k, v in r.items() if k != 'session'} for r in store.list('review')]

    @app.post('/api/staff/reviews/{review_id}/reply')
    def reply(review_id: str, body: ReviewReply, actor=Depends(staff)):
        if not reply_allowed(body.reply):
            raise HTTPException(422, 'Use general guidance without personal identifiers, diagnosis or dosing.')
        with store.lock(review_id):
            review = store.get(review_id) if review_id.startswith('review:') else None
            if not review:
                raise HTTPException(404, 'Review not found or expired.')
            review.update(reply=body.reply.strip(), status='replied', reviewed_by=actor)
            store.put(review_id, 'review', review)
            conversation = store.get(review['session'])
            if conversation and conversation.get('user_id'):
                service.save_message(review['session'], conversation['user_id'], 'staff', body.reply.strip(), actor)
        return {'ok': True}

    @app.get('/api/staff/sources')
    def sources(actor=Depends(staff)):
        return knowledge.sources()

    @app.post('/api/staff/sources')
    def add_source(body: SourceInput, actor=Depends(staff)):
        return {'id': knowledge.save(body, actor)}

    @app.patch('/api/staff/sources/{source_id}')
    def decide_source(source_id: str, body: SourceDecision, actor=Depends(staff)):
        with store.lock(source_id):
            source = store.get(source_id) if source_id.startswith('source:') else None
            if not source or source['hospital_id'] != settings.hospital_id:
                raise HTTPException(404, 'Source not found.')
            source.update(approved=body.approved, reviewed_by=actor)
            store.put(source_id, 'source', source, permanent=True)
        return {'ok': True}

    @app.post('/webhooks/telegram')
    async def telegram(request: Request):
        if not settings.telegram_bot_token:
            raise HTTPException(404, 'Telegram is disabled.')
        supplied = request.headers.get('x-telegram-bot-api-secret-token', '')
        if not hmac.compare_digest(supplied, settings.telegram_webhook_secret):
            raise HTTPException(403, 'Invalid webhook signature.')
        try:
            data = await request.json()
            if not isinstance(data, dict):
                raise ValueError()
            await run_in_threadpool(channels.telegram, data)
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(400, 'Invalid webhook payload.') from None
        return {'ok': True}

    @app.get('/webhooks/whatsapp')
    def verify_whatsapp(request: Request):
        params = request.query_params
        if not settings.whatsapp_enabled:
            raise HTTPException(404, 'WhatsApp is disabled.')
        if params.get('hub.mode') != 'subscribe' or not hmac.compare_digest(params.get('hub.verify_token', ''), settings.whatsapp_verify_token):
            raise HTTPException(403, 'Invalid verification token.')
        return PlainTextResponse(params.get('hub.challenge', ''))

    @app.post('/webhooks/whatsapp')
    async def whatsapp(request: Request):
        if not settings.whatsapp_enabled:
            raise HTTPException(404, 'WhatsApp is disabled.')
        raw = await request.body()
        expected = 'sha256=' + hmac.new(settings.whatsapp_app_secret.encode(), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(request.headers.get('x-hub-signature-256', ''), expected):
            raise HTTPException(403, 'Invalid webhook signature.')
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError()
            await run_in_threadpool(channels.whatsapp, data)
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(400, 'Invalid webhook payload.') from None
        return {'ok': True}

    def get_hybrid_index():
        if knowledge._hybrid is None:
            knowledge._hybrid = HybridIndex(settings)
        return knowledge._hybrid

    @app.post('/api/rag/embed')
    def rag_embed(body: EmbedRequest):
        idx = get_hybrid_index()
        dense_vecs = [v.tolist() for v in idx.dense.embed(body.texts)]
        sparse_vecs = [
            {'indices': s.indices.tolist(), 'values': s.values.tolist()}
            for s in idx.sparse.embed(body.texts)
        ]
        return {'dense': dense_vecs, 'sparse': sparse_vecs}

    @app.post('/api/rag/rerank')
    def rag_rerank(body: RerankRequest):
        idx = get_hybrid_index()
        if not idx.reranker:
            return {'ranked': [{'index': i, 'passage': p, 'score': 1.0} for i, p in enumerate(body.passages[:body.top_k])]}
        scores = list(idx.reranker.rerank(body.query, body.passages))
        ranked = sorted(
            [{'index': i, 'passage': p, 'score': float(s)} for i, (p, s) in enumerate(zip(body.passages, scores))],
            key=lambda x: x['score'],
            reverse=True
        )
        return {'ranked': ranked[:body.top_k]}

    @app.post('/api/rag/search')
    def rag_search(body: RagSearchRequest):
        idx = get_hybrid_index()
        results = idx.search_hybrid_pipeline(body.query, body.category, top_k=body.top_k)
        return {'results': [
            {
                'source_id': r['source_id'],
                'title': r['title'],
                'url': r['url'],
                'category': r['category'],
                'text': r['text'],
                'expanded_text': r['expanded_text'],
                'rerank_score': r.get('rerank_score', 0.0),
                'metadata': r.get('metadata', {})
            }
            for r in results
        ]}

    @app.post('/api/rag/ingest')
    def rag_ingest(actor=Depends(staff)):
        sources = knowledge.active('hospital') + knowledge.active('health')
        idx = get_hybrid_index()
        idx.index(sources)
        return {'ok': True, 'indexed_count': len(sources)}

    app.mount('/static', StaticFiles(directory=ROOT / 'web'), name='static')

    @app.get('/')
    def index():
        return FileResponse(ROOT / 'web/index.html')

    @app.get('/staff')
    def staff_page():
        return FileResponse(ROOT / 'web/staff.html')

    return app
