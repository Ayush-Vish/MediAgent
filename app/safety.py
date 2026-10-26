"""Non-semantic safety helpers for LLM guardrail decisions."""
import unicodedata

from app.schemas import Answer, SeverityInfo


def normalize(text):
    """Normalize Unicode and whitespace for search/indexing, not moderation."""
    return ' '.join(''.join(
        character for character in unicodedata.normalize('NFKC', text)
        if unicodedata.category(character) != 'Cf'
    ).lower().replace('\u2019', "'").split())


def answer_for_input_decision(decision):
    """Map an LLM policy label to a stable user-facing response."""
    severity = SeverityInfo(
        level=decision.severity,
        confidence=decision.confidence,
        summary=decision.summary,
    )
    if decision.action == 'emergency':
        return Answer(
            route='emergency',
            text=(
                '🚨 **URGENT MEDICAL NOTICE**: This may require immediate medical attention. '
                'Please contact local emergency services (112 / 108 / 911) or go to the nearest '
                'emergency department now. Do not wait for an online reply.'
            ),
            severity=severity,
            evidence='none',
        )
    if decision.action == 'privacy':
        return Answer(
            route='privacy',
            text=('Please remove names, contact details, hospital numbers, dates of birth, and other '
                  'personal identifiers, then ask the question again without patient records.'),
            severity=severity,
            evidence='none',
        )
    if decision.action == 'blocked':
        return Answer(
            route='blocked',
            text=('I can help with hospital information and general health education, but I cannot '
                  'follow requests to bypass safeguards or reveal private instructions or secrets.'),
            severity=severity,
            evidence='none',
        )
    if decision.action == 'handoff':
        return Answer(
            route='handoff',
            text=('A qualified clinician or pharmacist needs to assess personal diagnosis, treatment, '
                  'and dosing decisions. Please contact your care team.'),
            severity=severity,
            evidence='none',
        )
    if decision.action == 'out_of_scope':
        return Answer(
            route='health',
            text=('I can help with general health education and hospital questions. What health or '
                  'hospital topic would you like to ask about?'),
            severity=severity,
            evidence='none',
        )
    return None
