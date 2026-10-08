import logging
from app.observability import traced, event
import re
from datetime import date
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context

from app.knowledge import TOPICS, citation, topic_for
from app.safety import HEALTH, assess_severity, guard, normalize, reply_allowed
from app.schemas import Answer, SeverityInfo
from app.providers import configured_provider
from app.llm_pool import LLMUnavailable
from app.key_pool import PoolUnavailable

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
    severity: dict | None
    alert: dict | None
    guard_decision: object | None
    is_greeting: bool
    is_human_request: bool


class ChatGraph:
    def __init__(self, knowledge, store, settings):
        self.knowledge, self.store, self.settings = knowledge, store, settings
        self.provider, self.provider_config = configured_provider(settings)
        builder = StateGraph(ChatState)
        builder.add_node('route', self.route)
        builder.add_node('retrieve', self.retrieve)
        builder.add_node('respond', self.respond)
        
        # Purely sequential flow through the graph: START -> route -> retrieve -> respond -> END
        # No immediate answer short-circuiting out of route
        builder.add_edge(START, 'route')
        builder.add_edge('route', 'retrieve')
        builder.add_edge('retrieve', 'respond')
        builder.add_edge('respond', END)
        self.graph = builder.compile()

    @traced('routing')
    def route(self, state):
        query = state['query']

        # Guard checks: emergency, PII leaks, injections, treatment requests
        guard_decision = guard(query)

        # Greetings check
        is_greeting = normalize(query) in {'hi', 'hello', 'hey', '/start', 'help'}

        # Human/staff review request
        is_human_request = bool(re.search(r'\b(human|staff|person|agent)\b', normalize(query)))

        # Severity assessment
        severity = assess_severity(query)
        severity_dict = {'level': severity.level, 'confidence': severity.confidence,
                         'summary': severity.summary}

        # Topic detection
        topic = topic_for(query)
        if not topic and normalize(query) in {'tell me more', 'more', 'and then', 'what next'}:
            topic = state.get('previous_topic')
            if topic:
                query = TOPICS[topic][1]

        # Category determination
        if topic in ('appointment', 'preparation', 'contact', 'insurance', 'campus', 'changes'):
            category = 'hospital'
        elif topic in ('medicine', 'cough', 'fever'):
            category = 'health'
        elif re.search(r'\b(hospital|appointment|book|booking|visit\w*|opd|ward|admit\w*|admission|discharge|billing|cost|price|tariff|fee|insurance|document\w*|parking|reception|counter|helpdesk|contact|timing|hour|campus|cmc|vellore|portal|registration)\b', normalize(query)) and not HEALTH.search(normalize(query)):
            category = 'hospital'
        else:
            category = 'health'

        return {
            'query': query,
            'topic': topic,
            'category': category,
            'severity': severity_dict,
            'guard_decision': guard_decision,
            'is_greeting': is_greeting,
            'is_human_request': is_human_request,
        }

    @traced('context.retrieval')
    def retrieve(self, state):
        # If guard blocked, greeting, or staff handoff, skip knowledge search
        if state.get('guard_decision') or state.get('is_greeting') or state.get('is_human_request'):
            return {'sources': [], 'mode': 'local'}

        sources, mode = self.knowledge.search(state['query'], state['category'])
        seen, unique = set(), []
        for source in sources:
            if source['url'] not in seen:
                seen.add(source['url'])
                unique.append(source)
        return {'sources': unique[:2], 'mode': mode}

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
            sources_selected, selected = self.select_evidence(topic, sources)
            citations = [citation(source, i + 1) for i, source in enumerate(sources_selected)]
            text = self.generate_answer(query, category, sources_selected, state.get('context', []))
            if text is not None:
                generated = True
                evidence = 'supported'
            elif category == 'hospital' or self.provider.name == 'local':
                text = '\n\n'.join(f'{c.excerpt} [{c.id}]' for c in citations)
                evidence = 'supported'

        # If not answered by sources and category is health, call AI general answer
        if not text and category == 'health':
            text = self.generate_general_answer(query, state.get('context', []))
            if text is not None:
                generated = True
                evidence = 'general'
                citations = []
            elif sources and self.provider.name == 'local':
                text = '\n\n'.join(f'{c.excerpt} [{c.id}]' for c in citations)
                evidence = 'supported'
            else:
                text = ('I am here to help with health questions. For this specific symptom or condition, '
                        'I recommend consulting a qualified healthcare professional.')
                evidence = 'general'
                citations = []

        if not text:
            text = ('I do not have enough verified information to answer this question. '
                    'Please consult a healthcare professional.')
            evidence = 'none'

        # Severity warnings
        if severity_level == 'emergency':
            emergency_prefix = (
                '🚨 **EMERGENCY NOTICE**: This sounds like it could require immediate medical '
                'attention. Please call emergency services (112 / 108 / 911) or go to the '
                'nearest emergency room right away. Do not wait for an online reply.\n\n'
            )
            text = emergency_prefix + text
        elif severity_level == 'severe' and severity_confidence >= ALERT_THRESHOLD:
            warning_prefix = (
                '⚠️ **Important Health Notice**: Based on your description, this may indicate a '
                'severe condition needing prompt medical attention. Please consult a doctor as soon as possible.\n\n'
            )
            text = warning_prefix + text

        # Health disclaimer
        if category == 'health' and '⚕️' not in text:
            text += ('\n\n⚕️ *This is general health education only, not a medical diagnosis '
                     'or personal treatment plan. Always consult a doctor for personal care.*')

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
            if not isinstance(text, str) or not text or len(text) > 4000 or not reply_allowed(text):
                log.info('AI answer rejected: is_str=%s, len=%s, allowed=%s', isinstance(text, str), len(text) if text else 0, reply_allowed(text) if text else False)
                return None
            # Normalize full-width or variant brackets emitted by LLMs (e.g. 【S1】 -> [S1])
            text = re.sub(r'[【〔（\(](S\d+)[】〕）\)]', r'[\1]', text)
            valid = {f'S{i + 1}' for i in range(len(sources))}
            cited = set(re.findall(r'\[(S\d+)\]', text))
            event('answer.citations', valid=bool(cited) and cited.issubset(valid), count=len(cited))
            if not cited or not cited.issubset(valid):
                log.info('AI answer citations invalid: cited=%s, valid=%s', cited, valid)
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
            if not isinstance(text, str) or not text or len(text) > 4000 or not reply_allowed(text):
                log.info('AI general answer rejected: is_str=%s, len=%s, allowed=%s', isinstance(text, str), len(text) if text else 0, reply_allowed(text) if text else False)
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
