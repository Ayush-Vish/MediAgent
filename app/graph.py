import logging
from app.observability import traced, event
from datetime import date
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context

from app.guardrails import LLMGuardrails
from app.knowledge import TOPICS, citation
from app.safety import answer_for_input_decision
from app.schemas import Answer, SeverityInfo
from app.providers import configured_provider
from app.llm_pool import LLMUnavailable
from app.key_pool import PoolUnavailable
from app.web_search import WebSearchAgent, sanitize_search_query
from app.streaming import emit_status

log = logging.getLogger(__name__)

ALERT_THRESHOLD = 0.7


class ChatState(TypedDict, total=False):
    context: list[dict]
    query: str
    previous_topic: str | None
    topic: str | None
    category: str
    answer: dict
    sources: list
    mode: str
    local_sources: list
    web_sources: list
    local_mode: str
    web_mode: str
    language: str
    severity: dict | None
    alert: dict | None
    guard_decision: object | None
    is_greeting: bool
    is_human_request: bool


def _format_local_response(language: str, citations: list, hospital_name: str, web: bool = False) -> str:
    items = []
    for c in citations:
        body = ' '.join(c.excerpt.replace(
            "Read the linked source for full context.", ""
        ).split())
        items.append(f"• **{c.title}**: {body} [{c.id}]")

    joined_items = '\n\n'.join(items)
    if web:
        if language == 'hinglish':
            return f"Trusted public medical sources se mili jaankari:\n\n{joined_items}"
        if language == 'hindi':
            return f"विश्वसनीय सार्वजनिक चिकित्सा स्रोतों से मिली जानकारी:\n\n{joined_items}"
        return f"Based on trusted public medical sources, here is the key information:\n\n{joined_items}"
    if language == 'hinglish':
        return (
            f"Yahan {hospital_name} ki official guidelines ke anusar zaroori jaankari di gayi hai:\n\n"
            f"{joined_items}"
        )
    if language == 'hindi':
        return f"{hospital_name} के आधिकारिक स्रोतों के अनुसार जानकारी:\n\n{joined_items}"
    return (
        f"Based on official guidelines from {hospital_name}, here is the key information:\n\n"
        f"{joined_items}"
    )


class ChatGraph:
    def __init__(self, knowledge, store, settings, web_search_agent=None):
        self.knowledge, self.store, self.settings = knowledge, store, settings
        self.provider, self.provider_config = configured_provider(settings)
        self.guardrails = LLMGuardrails(settings)
        self.web_search_agent = web_search_agent or WebSearchAgent(settings)
        builder = StateGraph(ChatState)
        builder.add_node('route', self.route)
        builder.add_node('retrieve', self.retrieve)
        builder.add_node('web_search', self.web_search)
        builder.add_node('merge_evidence', self.merge_evidence)
        builder.add_node('respond', self.respond)
        builder.add_node('moderate_output', self.moderate_output)
        
        # Local and trusted-web retrieval are independent, so run them in
        # parallel and join both branches before answer generation.
        builder.add_edge(START, 'route')
        builder.add_edge('route', 'retrieve')
        builder.add_edge('route', 'web_search')
        builder.add_edge(['retrieve', 'web_search'], 'merge_evidence')
        builder.add_edge('merge_evidence', 'respond')
        builder.add_edge('respond', 'moderate_output')
        builder.add_edge('moderate_output', END)
        self.graph = builder.compile()

    @traced('routing')
    def route(self, state):
        query = state['query']
        context = state.get('context', [])
        decision = self.guardrails.inspect_input(query, context, state.get('previous_topic'))
        topic = decision.topic if decision.topic in TOPICS else None
        guard_decision = answer_for_input_decision(decision)

        return {
            'query': query,
            'topic': topic,
            'category': decision.category,
            'severity': {'level': decision.severity, 'confidence': decision.confidence,
                         'summary': decision.summary},
            'guard_decision': guard_decision,
            'is_greeting': decision.action == 'welcome',
            'is_human_request': decision.action == 'human_review',
            'language': decision.language,
            'context': context,
        }

    @traced('context.retrieval')
    def retrieve(self, state):
        # If guard blocked, greeting, or staff handoff, skip knowledge search
        if state.get('guard_decision') or state.get('is_greeting') or state.get('is_human_request'):
            return {'local_sources': [], 'local_mode': 'local'}

        if not self.settings.hybrid_enabled:
            emit_status('knowledge', 'Searching approved medical sources…')

        search_query = state['query']
        context = state.get('context', [])
        previous_user_msg = next((m['text'] for m in reversed(context) if m.get('role') == 'user'), None)
        if previous_user_msg and len(search_query.split()) < 7:
            search_query = f"{previous_user_msg} {search_query}"
        elif len(search_query.split()) < 7 and state.get('topic') in TOPICS:
            # Anonymous raw messages intentionally live only in process memory,
            # so a restart can leave us with a durable topic but no prior text.
            # Expand short follow-ups from that public topic rather than
            # searching literal phrases such as "tell me more".
            search_query = f"{TOPICS[state['topic']][1]} {search_query}"

        sources, mode = self.knowledge.search(search_query, state['category'])
        seen, unique = set(), []
        for source in sources:
            if source['url'] not in seen:
                seen.add(source['url'])
                unique.append(source)
        return {'local_sources': unique[:2], 'local_mode': mode}

    @traced('web_search')
    def web_search(self, state):
        """Add allowlisted web evidence alongside approved local evidence."""
        if not self.settings.web_search_enabled:
            event('web_search.skipped', reason='disabled')
            return {}
        if state.get('guard_decision'):
            event('web_search.skipped', reason='guarded')
            return {}
        if state.get('is_greeting'):
            event('web_search.skipped', reason='greeting')
            return {}
        if state.get('is_human_request'):
            event('web_search.skipped', reason='human_handoff')
            return {}

        category = state.get('category', 'health')
        query = state['query']
        context = state.get('context', [])

        emit_status('web', 'Searching trusted medical websites…')
        # A recognized topic is already a stable public search query. Reserve
        # the LLM rewrite for unknown/ambiguous medical terminology.
        rewritten = None if state.get('topic') else self.rewrite_search_query(query, category, context)
        search_query = sanitize_search_query(rewritten) or sanitize_search_query(query)
        if not search_query:
            event('web_search.skipped', reason='invalid_query')
            return {}
        web_sources = self.web_search_agent.search(search_query, category)
        if not web_sources:
            event('web_search.empty', category=category)
            return {'web_sources': [], 'web_mode': ''}
        query_mode = 'llm-query' if rewritten and sanitize_search_query(rewritten) else 'direct-query'
        event('web_search.merge', category=category, count=len(web_sources), query_mode=query_mode)
        return {'web_sources': web_sources, 'web_mode': f'web ({query_mode})'}

    @traced('evidence.merge')
    def merge_evidence(self, state):
        sources, seen = [], set()
        for source in [*state.get('local_sources', []), *state.get('web_sources', [])]:
            if source['url'] not in seen:
                seen.add(source['url'])
                sources.append(source)
        modes = [mode for mode in (state.get('local_mode'), state.get('web_mode')) if mode]
        return {'sources': sources, 'mode': ' + '.join(modes) or 'local'}

    @traced('llm.search_query')
    def rewrite_search_query(self, query, category, context=None):
        provider, provider_config = configured_provider(self.settings)
        self.provider, self.provider_config = provider, provider_config
        if provider.name == 'local' or provider_config is None:
            return None
        quota_key = f'quota:ai:{provider.name}:' + date.today().isoformat()
        if not self.store.consume(quota_key, provider_config.daily_limit, 86400):
            return None
        try:
            return provider.rewrite_search_query(query, category, context)
        except (LLMUnavailable, PoolUnavailable):
            return None
        except Exception as error:
            log.warning('Search query rewriting unavailable: %s', type(error).__name__)
            return None

    @traced('llm.evidence_selection')
    def select_evidence(self, topic, sources):
        provider, provider_config = configured_provider(self.settings)
        self.provider, self.provider_config = provider, provider_config
        if topic not in TOPICS or provider.name == 'local' or provider_config is None:
            return sources, False
        limit = provider_config.daily_limit
        quota_key = f'quota:ai:{provider.name}:' + date.today().isoformat()
        if not self.store.consume(quota_key, limit, 86400):
            return sources, False
        try:
            ids = provider.select(topic, sources)
            if ids:
                return [sources[i] for i in ids], True
        except Exception:
            log.warning('Evidence selector unavailable; using local excerpts')
        return sources, False

    @traced('answer')
    def respond(self, state):
        emit_status('answer', 'Assembling your answer…')
        query = state['query']
        guard_decision = state.get('guard_decision')
        severity = state.get('severity')
        severity_level = severity.get('level', 'general') if severity else 'general'
        severity_confidence = severity.get('confidence', 0.0) if severity else 0.0

        # Build alert payload for staff when severe or emergency
        alert = None
        if severity_level == 'emergency' or (severity_level == 'severe' and severity_confidence >= ALERT_THRESHOLD):
            alert = {
                'severity': severity_level,
                'confidence': severity_confidence,
                'summary': severity.get('summary', 'Health concern detected') if severity else 'Health concern detected',
                'user_query': query,
            }

        # 1. Guard check handling
        if guard_decision:
            if guard_decision.route == 'emergency':
                guard_decision.severity = SeverityInfo(
                    level='emergency',
                    confidence=severity_confidence,
                    summary=severity.get('summary', '') if severity else ''
                )
            return {'answer': guard_decision.model_dump(), 'alert': alert}

        # 2. Greeting handling
        if state.get('is_greeting'):
            ans = Answer(
                route='welcome',
                text=(f'Welcome to MediAgent! I can help you with general health '
                      f'information, {self.settings.hospital_name} hospital queries, '
                      f'and medical education. Ask me anything about health symptoms, '
                      f'conditions, medications, or hospital services. '
                      f'⚠️ For emergencies, please call emergency services (112/108/911) immediately. '
                      f'Please do not share personal identifiers or patient records.')
            )
            return {'answer': ans.model_dump(), 'alert': None}

        # 3. Human staff review request
        if state.get('is_human_request'):
            ans = self.abstain(
                'You can request a demo staff review here. This queue is not connected '
                'to the hospital; use its official patient portal for assistance.'
            )
            return {'answer': ans.model_dump(), 'alert': None}

        category = state.get('category', 'health')
        sources = state.get('sources', [])

        # 4. Hospital questions without approved sources -> abstain (handoff)
        if category == 'hospital' and not sources:
            return {'answer': self.abstain(
                'I do not have current, approved information that answers this question. '
                'Please check the official hospital website or contact the patient helpdesk. '
                'You can also request a demo staff review.'
            ).model_dump(), 'alert': None}

        # 5. Build response via AI
        text = None
        citations = []
        generated = False
        selected = False
        evidence = 'none'

        topic = state.get('topic')
        if sources:
            web_sources = any(source.get('web') for source in sources)
            sources_selected, selected = self.select_evidence(topic, sources)
            citations = [citation(source, i + 1) for i, source in enumerate(sources_selected)]
            text = self.generate_answer(query, category, sources_selected, state.get('context', []))
            if text is not None:
                generated = True
                evidence = 'limited' if web_sources else 'supported'
            elif sources:
                # Keep a rejected model draft relevant by falling back to the
                # retrieved excerpts instead of unrelated generic wording.
                text = _format_local_response(state.get('language', 'english'), citations,
                                              self.settings.hospital_name, web_sources)
                evidence = 'limited' if web_sources else 'supported'

        # If not answered by sources and category is health, call AI general answer
        if not text and category == 'health':
            text = self.generate_general_answer(query, state.get('context', []))
            if text is not None:
                generated = True
                evidence = 'general'
                citations = []
            elif sources and self.provider.name == 'local':
                web_sources = any(source.get('web') for source in sources)
                text = _format_local_response(state.get('language', 'english'), citations,
                                              self.settings.hospital_name, web_sources)
                evidence = 'limited' if web_sources else 'supported'
            else:
                text = ('I am here to help with health questions. For this specific symptom or condition, '
                        'I recommend consulting a qualified healthcare professional.')
                evidence = 'general'
                citations = []

        if not text:
            text = ('I do not have enough verified information to answer this question. '
                    'Please consult a healthcare professional.')
            evidence = 'none'

        severity_info = SeverityInfo(
            level=severity_level,
            confidence=severity_confidence,
            summary=severity.get('summary', '') if severity else ''
        )

        mode_str = state.get('mode', 'lexical')
        if selected:
            mode_str += f' + AI selection ({self.provider.name})'
        if generated:
            mode_str += f' + AI answer ({self.provider.name})'

        answer = Answer(
            text=text,
            route='emergency' if severity_level == 'emergency' else category,
            sources=citations,
            evidence=evidence,
            mode=mode_str,
            severity=severity_info
        )

        return {'answer': answer.model_dump(), 'alert': alert}

    @traced('guardrail.output')
    def moderate_output(self, state):
        answer = dict(state['answer'])
        emit_status('answer', 'Checking answer safety…')
        decision = self.guardrails.inspect_output(
            state['query'], answer['text'], state.get('category', 'health'),
            answer.get('sources', []),
            (state.get('severity') or {}).get('level', 'general'),
        )
        citations = answer.get('sources', [])
        answer['sources'] = [
            citation for index, citation in enumerate(citations)
            if index in decision.safe_source_ids
        ]
        if decision.allowed:
            answer['mode'] = f"{answer.get('mode', 'local')} + LLM guardrail"
            return {'answer': answer}
        answer.update(
            text=decision.replacement.strip(),
            mode=f"{answer.get('mode', 'local')} + LLM guardrail rewrite",
        )
        return {'answer': answer}

    @traced('llm.grounded_answer')
    def generate_answer(self, query, category, sources, context=None):
        provider, provider_config = configured_provider(self.settings)
        self.provider, self.provider_config = provider, provider_config
        if provider.name == 'local' or provider_config is None:
            return None
        quota_key = f'quota:ai:{provider.name}:' + date.today().isoformat()
        if not self.store.consume(quota_key, provider_config.daily_limit, 86400):
            if provider.name == 'pool':
                raise LLMUnavailable()
            return None
        try:
            text = provider.generate(query, category, sources, context)
            if not isinstance(text, str) or not text or len(text) > 4000:
                log.info('AI answer structurally invalid: is_str=%s, len=%s',
                         isinstance(text, str), len(text) if text else 0)
                return None
            return text
        except (LLMUnavailable, PoolUnavailable):
            raise LLMUnavailable() from None
        except Exception as e:
            log.warning('AI answer generation unavailable: %s', e)
            return None

    @traced('llm.general_answer')
    def generate_general_answer(self, query, context=None):
        """Generate a general health answer when no curated sources match."""
        provider, provider_config = configured_provider(self.settings)
        self.provider, self.provider_config = provider, provider_config
        if provider.name == 'local' or provider_config is None:
            return None
        quota_key = f'quota:ai:{provider.name}:' + date.today().isoformat()
        if not self.store.consume(quota_key, provider_config.daily_limit, 86400):
            if provider.name == 'pool':
                raise LLMUnavailable()
            log.warning('AI general answer quota exceeded for %s', quota_key)
            return None
        try:
            text = provider.generate_general(query, context)
            if not isinstance(text, str) or not text or len(text) > 4000:
                log.info('AI general answer structurally invalid: is_str=%s, len=%s',
                         isinstance(text, str), len(text) if text else 0)
                return None
            return text
        except (LLMUnavailable, PoolUnavailable):
            raise LLMUnavailable() from None
        except Exception as e:
            log.warning('AI general answer generation unavailable: %s', e)
            return None

    @staticmethod
    def abstain(text):
        return Answer(text=text, route='handoff', evidence='none')

    def run(self, query, previous_topic=None, context=None):
        # Never allow inherited tracing configuration to export a user's message.
        with tracing_context(enabled=False):
            return self.graph.invoke({'query': query, 'previous_topic': previous_topic, 'context': context or []})
