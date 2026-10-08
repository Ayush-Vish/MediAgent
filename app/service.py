import hashlib
import json
import re
import secrets
import time
from collections import OrderedDict
from uuid import uuid4

from app.knowledge import TOPICS
from app.observability import traced, event, user_query


def session_key(token):
    return 'session:' + hashlib.sha256(token.encode()).hexdigest()


class ChatService:
    def __init__(self, store, graph):
        self.store, self.graph = store, graph
        # Anonymous text stays in bounded process memory, not persisted history.
        self.recent_messages = OrderedDict()

    def create_session(self, token=None):
        token = token or secrets.token_urlsafe(32)
        key = session_key(token)
        if not self.store.get(key):
            self.recent_messages.pop(key, None)
            self.store.put(key, 'session', {'turns': [], 'topic': None, 'created': time.time()})
        return token

    @traced('chat')
    def chat(self, token, message, request_id, user=None):
        user_query(message)
        key = session_key(token)
        with self.store.lock(key):
            session = self.store.get(key)
            if not session:
                raise LookupError('Session expired. Start a new conversation.')
            owner = session.get('user_id')
            if owner and (not user or user['id'] != owner):
                raise LookupError('Start a new conversation for this patient.')
            if user:
                session['user_id'] = user['id']
            for turn in session['turns']:
                if turn['request_id'] == request_id:
                    event('chat.cached')
                    return turn['answer']
            if not self.store.consume('quota:' + key, 15, 60):
                raise OverflowError('Please wait a minute before sending more messages.')
            if user:
                previous = [m for m in self.store.list('patient_message')
                            if m['conversation_id'] == key and m['user_id'] == user['id']]
                previous.sort(key=lambda m: (m['timestamp'], m['id']))
                context = [{'role': m['role'], 'text': m['text']} for m in previous[-3:]]
            else:
                context = list(self.recent_messages.get(key, []))
            event('chat.context', message_count=len(context), limit=3)
            result = self.graph.run(message, session.get('topic'), context=context)
            answer = result['answer']
            topic = result.get('topic')
            alert = result.get('alert')

            if alert:
                # Dispatch an alert record to the staff queue
                review_id = 'review:' + str(uuid4())
                sev_level = alert['severity']
                confidence_pct = int(alert['confidence'] * 100)
                summary = alert['summary']

                # Redact potential personal names/phone numbers from the snippet shown to staff
                sanitized_msg = re.sub(r'\b(my name is|called|named)\s+\w+', '', message, flags=re.IGNORECASE).strip()
                user_label = f"Patient: {user['name']} (Ph: {user.get('phone') or 'N/A'})" if user else "Anonymous Patient"
                staff_question = f"[{sev_level.upper()} · {confidence_pct}% conf] [{user_label}] {summary} — User query: \"{sanitized_msg[:120]}\""

                review = {
                    'id': review_id,
                    'session': key,
                    'status': 'urgent' if sev_level == 'emergency' else 'pending',
                    'severity': sev_level,
                    'confidence': alert['confidence'],
                    'summary': summary,
                    'question': staff_question,
                    'user_message': sanitized_msg,
                    'reply': None,
                    'user': {
                        'id': user['id'],
                        'name': user['name'],
                        'email': user['email'],
                        'phone': user.get('phone', ''),
                        'age': user.get('age'),
                        'gender': user.get('gender', ''),
                        'emergency_contact': user.get('emergency_contact', ''),
                        'emergency_phone': user.get('emergency_phone', ''),
                    } if user else None,
                    'created': time.time(),
                }
                self.store.put(review_id, 'review', review)
                answer['review_id'] = review_id

            # Persist symptom event for authenticated patient
            if user:
                event_id = f"symp_{uuid4().hex[:12]}"
                sev_info = answer.get('severity')
                if hasattr(sev_info, 'level'):
                    sev_level_val = sev_info.level
                    conf_val = getattr(sev_info, 'confidence', 0.0)
                    sum_val = getattr(sev_info, 'summary', '')
                elif isinstance(sev_info, dict):
                    sev_level_val = sev_info.get('level', 'general')
                    conf_val = sev_info.get('confidence', 0.0)
                    sum_val = sev_info.get('summary', '')
                else:
                    sev_level_val = 'general'
                    conf_val = 0.0
                    sum_val = ''

                symptom_event = {
                    'id': event_id,
                    'user_id': user['id'],
                    'user_name': user.get('name', 'Anonymous Patient'),
                    'user_email': user.get('email', ''),
                    'timestamp': time.time(),
                    'query': message,
                    'topic': topic,
                    'severity': sev_level_val,
                    'confidence': conf_val,
                    'summary': sum_val or (TOPICS.get(topic, ['', ''])[1] if topic else 'General inquiry'),
                    'route': answer.get('route', 'health'),
                    'answer_snippet': (answer.get('text') or '')[:300],
                }
                self.store.put(
                    f"symptom:{user['id']}:{int(time.time()*1000)}:{event_id}",
                    'symptom_event',
                    symptom_event,
                    permanent=True,
                )

            # Persist public intent, not arbitrary user prose or personal details.
            summary = TOPICS[topic][1] if topic else '[Message not retained for privacy]'
            session['topic'] = topic
            session['turns'].append({'request_id': request_id, 'question': summary, 'answer': answer,
                                     'created': time.time()})
            session['turns'] = session['turns'][-50:]
            self.store.put(key, 'session', session)
            if user:
                for role, text in [('user', message), ('assistant', answer['text'])]:
                    self.save_message(key, user['id'], role, text, answer=answer if role == 'assistant' else None)
            else:
                self.recent_messages[key] = (context + [
                    {'role': 'user', 'text': message}, {'role': 'assistant', 'text': answer['text']}])[-3:]
                self.recent_messages.move_to_end(key)
                while len(self.recent_messages) > 1000:
                    self.recent_messages.popitem(last=False)
            return answer

    def save_message(self, conversation, user_id, role, text, actor=None, answer=None):
        message = {'id': 'message:' + str(uuid4()), 'conversation_id': conversation,
                   'user_id': user_id, 'role': role, 'text': text,
                   'timestamp': time.time(), 'actor': actor, 'answer': answer}
        self.store.put(message['id'], 'patient_message', message)
        return message

    @traced('staff.conversation_history')
    def patient_messages(self, user_id):
        if not self.store.get(user_id):
            raise LookupError('Patient record not found.')
        messages = [m for m in self.store.list('patient_message') if m['user_id'] == user_id]
        return sorted(messages, key=lambda m: (m['timestamp'], m['id']))

    @traced('staff.doctor_message')
    def doctor_message(self, user_id, conversation, text, actor, request_id):
        with self.store.lock(conversation):
            session = self.store.get(conversation)
            if not session or session.get('user_id') != user_id:
                raise LookupError('This patient conversation has expired or does not exist.')
            receipt = 'doctor-send:' + hashlib.sha256((actor + conversation + request_id).encode()).hexdigest()
            saved = self.store.get(receipt)
            if saved:
                event('staff.doctor_message.cached')
                return saved
            message = self.save_message(conversation, user_id, 'staff', text.strip(), actor)
            self.store.put(receipt, 'doctor_receipt', message)
            event('staff.doctor_message.saved', role='staff', character_count=len(message['text']))
            return message

    @traced('llm.patient_summary')
    def generate_patient_summary(self, user_id, since_days):
        from app.llm_pool import LLMUnavailable
        report = self.get_patient_symptom_summary(user_id=user_id, since_days=since_days)
        timeline = report['timeline']
        event('llm.patient_summary.context', since_days=since_days, event_count=len(timeline),
              selected_count=min(len(timeline), 100), truncated=len(timeline) > 100)
        if not timeline:
            event('llm.patient_summary.skipped', reason='no_events')
            return {'text': 'No recorded events in this period.', 'event_count': 0, 'ai_generated': False}
        # Do not send patient names/contact details to the external provider.
        from app.providers import _public_query
        events = [{'timestamp': ev['timestamp'], 'reported': _public_query(ev['query']),
                   'recorded_severity': ev['severity']} for ev in timeline[-100:]]
        provider = self.graph.provider
        text = provider.summarize(json.dumps(events)) if hasattr(provider, 'summarize') else None
        if not text:
            raise LLMUnavailable()
        event('llm.patient_summary.generated', character_count=len(text), event_count=len(events))
        return {'text': text, 'event_count': len(events), 'ai_generated': True,
                'generated_at': time.time(), 'truncated': len(timeline) > 100}

    @traced('staff.patient_timeline')
    def get_patient_symptom_summary(self, user_id=None, email=None, since_days=30):
        """Aggregate patient symptom history over an interval for doctor appointment prep."""
        user = None
        if email:
            rec = self.store.get(f"user_email:{email.strip().lower()}")
            if rec:
                user = self.store.get(rec['user_id'])
        elif user_id:
            user = self.store.get(user_id if str(user_id).startswith('user:') else f"user:{user_id}")

        if not user:
            raise LookupError('Patient record not found.')
        target_user_id = user['id'] if user else (
            user_id if user_id and str(user_id).startswith('user:') else (f"user:{user_id}" if user_id else None)
        )
        if not target_user_id and not user:
            raise LookupError('Patient record not found.')

        all_events = self.store.list('symptom_event')
        cutoff = time.time() - (since_days * 86400) if since_days > 0 else 0

        patient_events = [
            ev for ev in all_events
            if ev.get('user_id') == target_user_id and ev.get('timestamp', 0) >= cutoff
        ]
        patient_events.sort(key=lambda x: x.get('timestamp', 0))

        counts = {'emergency': 0, 'severe': 0, 'moderate': 0, 'general': 0}
        severity_order = {'emergency': 4, 'severe': 3, 'moderate': 2, 'general': 1}
        peak_score = 1
        peak_level = 'general'
        topics = []

        for ev in patient_events:
            sev = ev.get('severity', 'general')
            counts[sev] = counts.get(sev, 0) + 1
            score = severity_order.get(sev, 1)
            if score > peak_score:
                peak_score = score
                peak_level = sev
            top = ev.get('topic')
            if top and top not in topics:
                topics.append(top)

        total = len(patient_events)
        if total == 0:
            progression = "No symptom consultations recorded in this time interval."
        elif total == 1:
            progression = f"Single consultation recorded with {peak_level} concern."
        else:
            first_sev = severity_order.get(patient_events[0].get('severity', 'general'), 1)
            last_sev = severity_order.get(patient_events[-1].get('severity', 'general'), 1)
            if last_sev > first_sev:
                progression = f"Escalating: Symptoms progressed from {patient_events[0].get('severity')} to {patient_events[-1].get('severity')}."
            elif last_sev < first_sev:
                progression = f"Improving: Severity decreased from {patient_events[0].get('severity')} to {patient_events[-1].get('severity')}."
            else:
                progression = f"Stable: Persistent {peak_level} severity across consultations."

        safe_user = {k: v for k, v in user.items() if k not in ('password_hash', 'salt')} if user else {'id': target_user_id, 'name': 'Patient'}

        return {
            'patient': safe_user,
            'since_days': since_days,
            'total_events': total,
            'peak_severity': peak_level,
            'severity_counts': counts,
            'frequent_topics': topics,
            'clinical_progression': progression,
            'timeline': patient_events,
        }

    @traced('staff.patient_directory')
    def list_patients(self):
        """List registered patients for doctor review."""
        users = self.store.list('user')
        safe_users = [
            {k: v for k, v in u.items() if k not in ('password_hash', 'salt')}
            for u in users
        ]
        safe_users.sort(key=lambda x: x.get('created_at', 0), reverse=True)
        return safe_users

    def handoff(self, token):
        key = session_key(token)
        with self.store.lock(key):
            session = self.store.get(key)
            if not session or not session['turns']:
                raise LookupError('Start a conversation before requesting review.')
            for review in self.store.list('review'):
                if review['session'] == key and review['status'] in ('pending', 'urgent'):
                    return review
            review_id = 'review:' + str(uuid4())
            review = {'id': review_id, 'session': key, 'status': 'pending',
                      'severity': 'general', 'confidence': 0.0, 'summary': 'Patient requested demo staff review',
                      'question': session['turns'][-1]['question'], 'reply': None,
                      'created': time.time()}
            self.store.put(review_id, 'review', review)
            return review

    def history(self, token, user=None):
        key = session_key(token)
        session = self.store.get(key)
        if not session:
            raise LookupError('Session expired.')
        if session.get('user_id') and (not user or user['id'] != session['user_id']):
            raise LookupError('Start a new conversation for this patient.')
        reviews = [{k: v for k, v in r.items() if k != 'session'}
                   for r in self.store.list('review') if r['session'] == key]
        messages = [m for m in self.store.list('patient_message')
                    if m['conversation_id'] == key] if user else []
        messages.sort(key=lambda m: (m['timestamp'], m['id']))
        return {'turns': session['turns'], 'reviews': reviews, 'messages': messages}

    def forget(self, token):
        key = session_key(token)
        self.recent_messages.pop(key, None)
        with self.store.lock(key):
            self.store.remove(key)
            for review in self.store.list('review'):
                if review['session'] == key:
                    self.store.remove(review['id'])
            for message in self.store.list('patient_message'):
                if message['conversation_id'] == key:
                    self.store.remove(message['id'])
