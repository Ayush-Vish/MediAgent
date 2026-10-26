import json
import logging

import pytest

from app.observability import JsonFormatter, logger, request_id, span
from tests.test_chat import ask
from io import StringIO
from rich.console import Console
from app.observability import PrettyHandler


@pytest.mark.parametrize('enabled', [True, False])
def test_cloud_console_content_flags(settings, monkeypatch, capsys, enabled):
    from app import observability
    from app.main import create_app

    # Reuse the isolated test database while exercising cloud startup logging.
    settings.environment = 'cloud'
    settings.log_show_query = enabled
    settings.log_show_chunks = enabled
    monkeypatch.setattr(observability, 'HAS_RICH', False)
    application = create_app(settings)
    try:
        observability.user_query('Example patient query')
        observability.retrieved_chunks([{'title': 'Source title', 'url': 'https://example.com',
                                        'text': 'Retrieved passage'}])
        observability.web_search_results('ear pain guidance', [
            {'title': 'NHS earache', 'url': 'https://www.nhs.uk/symptoms/earache/'}
        ])
        output = capsys.readouterr().err
        assert ('Example patient query' in output) is enabled
        assert ('Chunk 1: Source title' in output) is enabled
        assert ('Retrieved passage' in output) is enabled
        assert ('Web search query: ear pain guidance' in output) is enabled
        assert ('Web result 1: NHS earache' in output) is enabled
    finally:
        application.state.store.engine.dispose()
        observability.configure_logging(settings.log_file)


def test_query_excluded_from_json():
    record = logging.LogRecord('pipeline', logging.INFO, '', 0, '', (), None)
    record.event_data = {'event': 'chat.query', 'request_id': 'test-id'}
    record.query = 'Example patient query'
    assert record.query not in JsonFormatter().format(record)


def test_pretty_chunks_are_literal_and_excluded_from_json():
    output = StringIO()
    handler = PrettyHandler(Console(file=output, force_terminal=True, width=100, color_system='standard'))
    record = logging.LogRecord('pipeline', logging.INFO, '', 0, '', (), None)
    record.event_data = {'event': 'retrieval.chunks', 'request_id': 'test-id', 'count': 1}
    record.chunks = [{'title': '[red]Literal title[/red]', 'url': 'https://example.com',
                      'text': 'Approved source passage.'}]
    handler.emit(record)
    rendered = output.getvalue()
    assert '\x1b[' in rendered
    assert 'Chunk 1' in rendered and 'Approved source passage.' in rendered
    assert '[red]Literal title[/red]' in rendered
    assert 'Approved source passage.' not in JsonFormatter().format(record)


class Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.rows = []

    def emit(self, record):
        self.rows.append(json.loads(JsonFormatter().format(record)))


def test_trace_correlates_steps_without_patient_content(client):
    capture = Capture()
    logger.addHandler(capture)
    try:
        result = ask(client, 'How can I book an appointment?')
        trace_id = result.headers['X-Request-ID']
        rows = [r for r in capture.rows if r['request_id'] == trace_id]
        events = {r['event'] for r in rows}
        assert {'http.request.started', 'chat.started', 'routing.completed',
                'retrieval.result', 'answer.completed', 'http.response'} <= events
        assert 'How can I book' not in json.dumps(rows)
        assert any('duration_ms' in r for r in rows)
        next_response = ask(client, 'hi')
        assert next_response.headers['X-Request-ID'] != trace_id
    finally:
        logger.removeHandler(capture)


def test_error_trace_does_not_leak_exception_or_context(client):
    capture = Capture()
    logger.addHandler(capture)
    try:
        with pytest.raises(ValueError):
            with span('test.failure'):
                raise ValueError('private-patient-and-api-secret')
        assert request_id.get() is None
        assert capture.rows[-1]['error_type'] == 'ValueError'
        assert 'private-patient' not in json.dumps(capture.rows)
    finally:
        logger.removeHandler(capture)


def test_503_logged_with_request_id(client, app):
    app.state.service.graph.settings.ai_provider = 'pool'
    capture = Capture()
    logger.addHandler(capture)
    try:
        response = ask(client, 'How can I book an appointment?')
        assert response.status_code == 503
        rows = [r for r in capture.rows if r['request_id'] == response.headers['X-Request-ID']]
        assert any(r['event'] == 'llm.exhausted' for r in rows)
        assert any(r['event'] == 'http.response' and r['status'] == 503 for r in rows)
    finally:
        logger.removeHandler(capture)
