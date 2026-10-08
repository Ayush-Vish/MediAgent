import hashlib
from app.observability import traced, event, retrieved_chunks
import json
import logging
import math
import re
from collections import Counter
from datetime import date
from uuid import NAMESPACE_URL, uuid5

from app.config import ROOT
from app.schemas import Citation, SourceInput
from app.safety import INJECTION, normalize

log = logging.getLogger(__name__)
STOP = set(
    'a an the is are am was were be being been can could do does did have has had '
    'how what where when why who to for of and or but so if in on at by with about '
    'i my me we our you your he his she her they their it its '
    'feel feeling felt get getting got please tell just like '
    'hospital cmc vellore'.split()
)
TOPICS = {
    'appointment': ('appointment booking registration book opd outpatient visit availability', 'How can appointments be booked?'),
    'preparation': ('bring prepare preparation documents document identification photo id proof', 'What should visitors bring for an appointment?'),
    'contact': ('contact phone telephone email helpdesk helpline hours opening working timing time schedule opd', 'How can the patient helpdesk be contacted and when is it open?'),
    'fees': ('fee fees cost price charge charges rate rates amount money payment counter opad advance', 'What are the appointment consultation charges and fees?'),
    'emergency': ('emergency trauma casualty ambulance 24/7 ems accident urgent', 'What emergency and ambulance services are available?'),
    'insurance': ('insurance cashless tpa claim reimbursement', 'Where is insurance information available?'),
    'campus': ('campus location address directions town ranipet travel reach', 'Where is the hospital and its campus information?'),
    'changes': ('cancel refund change postpone reschedule unit department', 'What are the online appointment change policies?'),
    'emeds': ('emeds medicine prescription delivery post courier meds drugs', 'How can medicines be ordered by post via eMeds?'),
    'international': ('international foreign passport visa iro embassy abroad', 'What are the guidelines for international patients?'),
    'cough': ('cough coughing', 'What is general cough education?'),
    'fever': ('fever temperature flu cold chills body ache', 'What is general fever education?'),
    'medicine': ('medicine medication paracetamol acetaminophen dolo doloe cetirizine cetrazine citrazine antihistamine allergy difference brand active ingredient pain fever', 'What is the general medicine information for this medicine?'),
}


def tokens(text):
    words = re.findall(r'[a-z0-9]+', normalize(text))
    aliases = {'doloe': 'dolo', 'dolo650': 'dolo', 'acetaminophen': 'paracetamol',
               'citrazine': 'cetirizine', 'cetrazine': 'cetirizine', 'dizy': 'dizzy',
               'vomiting': 'vomit', 'vomited': 'vomit', 'vomits': 'vomit', 'emesis': 'vomit'}
    words = [aliases.get(word, word) for word in words]
    return [word[:-1] if word.endswith('s') and len(word) > 4 else word for word in words if word not in STOP]


def topic_for(query):
    words = set(tokens(query))
    scores = [(len(words & set(tokens(terms))), topic) for topic, (terms, _) in TOPICS.items()]
    count, topic = max(scores)
    return topic if count else None


def source_key(source, hospital_id):
    identity = f'{hospital_id}|{source["url"]}|{source["title"]}|{source.get("page")}'
    return 'source:' + str(uuid5(NAMESPACE_URL, identity))


class Knowledge:
    def __init__(self, store, settings):
        self.store, self.settings = store, settings
        self._hybrid = None

    def seed(self):
        if self.settings.hospital_id != 'cmc-vellore':
            return
        for source in json.loads((ROOT / 'data/sources.json').read_text(encoding='utf-8')):
            self.save(SourceInput(**source), actor='bundled public-source demo', approved=True)

    def save(self, source, actor, approved=False):
        data = source.model_dump(mode='json')
        data['url'] = str(source.url)
        key = source_key(data, self.settings.hospital_id)
        digest = hashlib.sha256(source.text.encode()).hexdigest()
        old = self.store.get(key)
        if old and all(old.get(k) == v for k, v in data.items()):
            return key
        data.update(id=key, hospital_id=self.settings.hospital_id, digest=digest,
                    approved=approved, reviewed_by=actor, revision=(old or {}).get('revision', 0) + 1)
        self.store.put(key, 'source', data, permanent=True)
        return key

    def sources(self):
        return [s for s in self.store.list('source') if s['hospital_id'] == self.settings.hospital_id]

    def active(self, category):
        today = date.today()
        return [s for s in self.sources() if s['approved'] and not s.get('conflict')
                and s['category'] == category and not INJECTION.search(normalize(s['text']))
                and 0 <= (today - date.fromisoformat(s['checked_at'])).days <= self.settings.source_max_age_days]

    @traced('retrieval')
    def search(self, query, category):
        documents = self.active(category)
        event('retrieval.corpus', category=category, count=len(documents), hybrid_enabled=self.settings.hybrid_enabled)
        if not documents:
            return [], 'lexical'
        topic = topic_for(query)
        original = set(tokens(query))
        if not original:
            return [], 'lexical'
        expanded = original | (set(tokens(TOPICS[topic][0])) if topic else set())
        corpus = [Counter(tokens(d['title'] + ' ' + d['text'])) for d in documents]
        frequencies = Counter(term for doc in corpus for term in doc)
        unsupported = {'visiting', 'parking', 'icu', 'surgery', 'price', 'cost', 'mri', 'availability'}
        if any(term in original and term not in frequencies for term in unsupported):
            return [], 'lexical'
        average = sum(sum(d.values()) for d in corpus) / len(corpus)
        ranked = []
        for source, counts in zip(documents, corpus):
            # Require original-query overlap; expansion alone cannot invent relevance.
            if not original.intersection(counts):
                continue
            length = sum(counts.values())
            score = sum(math.log(1 + (len(corpus) - frequencies[t] + .5) / (frequencies[t] + .5))
                        * counts[t] * 2.2 / (counts[t] + 1.2 * (.25 + .75 * length / average))
                        for t in expanded if counts[t])
            if topic and topic_for(source['title']) == topic:
                score *= 2
            score *= 1 + len(original.intersection(tokens(source['title'])))
            ranked.append((score, source))
        ranked.sort(key=lambda item: item[0], reverse=True)
        # For a direct health-topic query, prefer documents about that topic,
        # not documents that merely mention the symptom in their body text.
        # Keep all query terms: "blood vomit" must not become plain "vomit".
        direct = [(score, source) for score, source in ranked
                  if original.issubset(set(tokens(source['title'])))] if category == 'health' else []
        if direct:
            ranked = direct
            # Short topic questions start with the overview, not a tail chunk
            # that happens to repeat the symptom in a short passage.
            def chunk_order(item):
                match = re.search(r'chunk (\d+)\s*$', item[1]['title'])
                return (int(match.group(1)) if match else 1, -item[0])
            ranked.sort(key=chunk_order)
            event('retrieval.topic_filter', count=len(ranked), reason='direct_title_match')
        hits = [s for score, s in ranked if score > .25][:5]
        mode = 'lexical'
        if self.settings.hybrid_enabled:
            try:
                if self._hybrid is None:
                    from app.vector import HybridIndex
                    self._hybrid = HybridIndex(self.settings)
                ids = self._hybrid.search(query, category)
                # Database approval and freshness are authoritative, even for stale vectors.
                approved = {s['id']: s for s in documents}
                positions = {key: rank for rank, key in enumerate(ids)}
                lexical_positions = {s['id']: i for i, s in enumerate(hits)}
                candidates = dict.fromkeys([*lexical_positions, *ids])
                # Preserve direct-topic filtering while allowing semantic-only hits
                # when there is no exact title match.
                direct_ids = {s['id'] for _, s in direct}
                hits = [approved[key] for key in candidates if key in approved
                        and (not direct_ids or key in direct_ids)]
                scores = {s['id']: (1 / (60 + lexical_positions[s['id']]) if s['id'] in lexical_positions else 0)
                          + (1 / (60 + positions[s['id']]) if s['id'] in positions else 0) for s in hits}
                hits.sort(key=lambda s: scores[s['id']], reverse=True)
                mode = 'hybrid'
            except Exception:
                event('retrieval.fallback', reason='hybrid_unavailable', mode='lexical')
                log.warning('Hybrid retrieval unavailable; using local evidence')
        retrieved_chunks(hits[:3])
        return hits[:3], mode


def citation(source, index):
    # Preserve complete sentences: truncating a medical qualification can change its meaning.
    sentences = re.split(r'(?<=[.!?])\s+', source['text'].strip())
    selected, count = [], 0
    for sentence in sentences:
        size = len(sentence.split())
        if count + size > 80:
            break
        selected.append(sentence)
        count += size
    excerpt = ' '.join(selected) if selected else 'Please open the source to read this information in full context.'
    if len(selected) < len(sentences):
        excerpt += ' Read the linked source for full context.'
    return Citation(id=f'S{index}', title=source['title'], url=source['url'],
                    checked_at=source['checked_at'], page=source.get('page'), excerpt=excerpt)
