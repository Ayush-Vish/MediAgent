"""Safety guards and severity assessment for health queries."""
import re
import unicodedata
from dataclasses import dataclass

from app.schemas import Answer, SeverityInfo

# --- Regex patterns ---

EMERGENCY = re.compile(
    r"\b(chest pain|can'?t breathe|cannot breathe|not breathing|difficulty breathing|"
    r"trouble breathing|shortness of breath|coughing (up )?blood|vomiting blood|"
    r"unconscious|unresponsive|overdose|kill myself|suicid\w*|stroke|severe bleeding|blue lips|"
    r"heart attack|loss of consciousness|seizure|choking|anaphyla\w*)\b"
)
IDENTIFIER = re.compile(
    r'[\w.+-]+@[\w.-]+\.[a-z]{2,}|\b(?:\d[ -]?){8,16}\b|'
    r'\b(my name is|my address|hospital (id|number) is|date of birth|my dob)\b'
)
INJECTION = re.compile(
    r'ignore.{0,25}(instruction|previous|system)|reveal.{0,25}(prompt|secret|key)|'
    r'jailbreak|<\s*/?\s*(system|instruction)|bypass.{0,20}(safety|guard)'
)
PERSONAL = re.compile(
    r"\b(i|i'm|ive|i've|my|me|we|our|he|she|his|her|they|their|patient|child|baby|father|mother|husband|wife)\b"
)
HEALTH = re.compile(
    r'\b(cough|fever|headache|rash|vomit\w*|diarrh\w*|symptom\w*|pain|diabet\w*|'
    r'blood pressure|hypertension|medicine|medication|dose|dosage|antibiotic|'
    r'diagnos\w*|prescri\w*|infect\w*|cold|flu|stomach|throat|ache|injury|wound|'
    r'nausea|dizz\w*|allergy|allergic|breath\w*|gout|asthma|cancer|disease|illness|'
    r'syndrome|disorder|uric|joint|muscle|heart|kidney|liver|lung|skin|virus|bacteri\w*|'
    r'treat\w*|vitamin|deficien\w*|cramp|fatigue|swelling|swollen|bleed\w*)\b'
)
TREATMENT = re.compile(
    r'\b(dose|dosage|prescribe|diagnose me)\b|\b(should|can) i (take|stop|start|increase|decrease)\b'
)

# --- Severity assessment ---

@dataclass
class SeverityResult:
    level: str       # emergency, severe, moderate, general
    confidence: float
    summary: str


EMERGENCY_PATTERNS = [
    (re.compile(r"\b(chest pain|heart attack)\b"), 0.95, 'Possible cardiac emergency'),
    (re.compile(r"\b(can'?t breathe|cannot breathe|not breathing|difficulty breathing|trouble breathing|shortness of breath)\b"), 0.95, 'Respiratory distress'),
    (re.compile(r"\b(coughing (up )?blood|vomiting blood|blood in stool|rectal bleeding)\b"), 0.90, 'Possible internal bleeding'),
    (re.compile(r"\b(unconscious|unresponsive|not waking|passed out|collapsed)\b"), 0.95, 'Loss of consciousness'),
    (re.compile(r"\b(overdose|poison\w*)\b"), 0.90, 'Possible poisoning or overdose'),
    (re.compile(r"\b(suicid\w*|kill myself|end my life|want to die|self.?harm|hurt myself)\b"), 0.95, 'Mental health crisis'),
    (re.compile(r"\b(stroke|seizure|convulsion|fit)\b"), 0.90, 'Possible acute neurological event'),
    (re.compile(r"\b(severe bleeding|won'?t stop bleeding|heavy bleeding)\b"), 0.90, 'Severe bleeding'),
    (re.compile(r"\b(blue lips|turning blue|cyanosis)\b"), 0.95, 'Possible cyanosis'),
    (re.compile(r"\b(choking|can'?t swallow|throat (closing|swelling))\b"), 0.90, 'Possible airway obstruction'),
    (re.compile(r"\b(anaphyla\w*|severe allergic)\b"), 0.90, 'Possible anaphylaxis'),
]

SEVERE_PATTERNS = [
    (re.compile(r"\b(high fever|very high temperature)\b"), 0.80, 'High fever reported'),
    (re.compile(r"\bfever\b.{0,40}\b(3|three|four|4|five|5|several|many)\s*(day|week)"), 0.85, 'Persistent prolonged fever'),
    (re.compile(r"\b(severe (pain|headache|stomach|abdominal))\b"), 0.80, 'Severe pain reported'),
    (re.compile(r"\b(swelling.{0,20}(face|throat|tongue|lip))\b"), 0.85, 'Facial/throat swelling'),
    (re.compile(r"\b(can'?t (eat|drink|sleep|walk|move|stand)|unable to (eat|drink|sleep|walk|move))\b"), 0.75, 'Functional impairment'),
    (re.compile(r"\b(sudden (vision|hearing|speech) (loss|change|problem))\b"), 0.85, 'Sudden sensory change'),
    (re.compile(r"\b(persistent vomit\w*|constant (vomit|nausea|diarrh))\b"), 0.75, 'Persistent GI symptoms'),
    (re.compile(r"\b(lump|mass|growth|tumor)\b"), 0.70, 'Possible growth or mass'),
    (re.compile(r"\b(blood in (urine|sputum))\b"), 0.80, 'Blood in bodily fluids'),
    (re.compile(r"\b(stiff neck|neck stiffness).{0,30}fever\b"), 0.85, 'Possible meningitis signs'),
    (re.compile(r"\bfever\b.{0,30}\b(stiff neck|neck stiffness)\b"), 0.85, 'Possible meningitis signs'),
    (re.compile(r"\b(rash.{0,20}(spread|doesn'?t fade|won'?t fade))\b"), 0.80, 'Non-blanching or spreading rash'),
    (re.compile(r"\b(dehydrat\w*|haven'?t (eaten|drunk|urinated).{0,20}(day|hour))\b"), 0.75, 'Possible dehydration'),
]


def assess_severity(text):
    """Classify the severity of a health query using pattern matching."""
    normalized = normalize(text)

    # Check emergency patterns (highest priority)
    for pattern, confidence, summary in EMERGENCY_PATTERNS:
        if pattern.search(normalized):
            return SeverityResult('emergency', confidence, summary)

    # Check severe patterns
    best = None
    for pattern, confidence, summary in SEVERE_PATTERNS:
        if pattern.search(normalized):
            if best is None or confidence > best.confidence:
                best = SeverityResult('severe', confidence, summary)
    if best:
        return best

    # Moderate: any health-related query
    if HEALTH.search(normalized):
        return SeverityResult('moderate', 0.3, 'General health query')

    return SeverityResult('general', 0.0, '')


def normalize(text):
    return ' '.join(''.join(c for c in unicodedata.normalize('NFKC', text)
                           if unicodedata.category(c) != 'Cf').lower().replace('\u2019', "'").split())


def guard(message):
    """Guard checks for emergency, PII leaks, injections, and treatment advice requests."""
    text = normalize(message)
    if EMERGENCY.search(text):
        sev = assess_severity(text)
        return Answer(
            route='emergency',
            text=(
                '🚨 **URGENT MEDICAL NOTICE**: This sounds like it could require immediate medical attention. '
                'Please contact local emergency services (112 / 108 / 911) or go to the nearest emergency '
                'department right away. Do not wait for this chat. A notification has also been flagged for staff.'
            ),
            severity=SeverityInfo(level='emergency', confidence=sev.confidence, summary=sev.summary),
            evidence='none'
        )
    if IDENTIFIER.search(text):
        return Answer(route='privacy', text='Please remove names, contact details, hospital numbers and other personal identifiers. This public demo cannot accept patient records. Ask a general question instead.')
    if INJECTION.search(text):
        return Answer(route='blocked', text='I can help with hospital information and general health education. I cannot follow instructions to bypass controls or expose private data.')
    if TREATMENT.search(text):
        return Answer(route='handoff', text='A qualified clinician or pharmacist needs to assess personal treatment and doses. I cannot diagnose or prescribe. Please contact your care team; this demo review queue is not monitored by the hospital.')
    return None


def safe_to_store(message):
    text = normalize(message)
    return not (guard(message) or PERSONAL.search(text))


def reply_allowed(text):
    return not IDENTIFIER.search(normalize(text)) and not re.search(
        r'\b(you (have|should take|must take|should stop)|guaranteed cure|no need to see|take \d+|\d+\s*mg)\b', normalize(text))
