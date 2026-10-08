import time

import httpx
import pytest

from app.config import Settings
from app.schemas import SourceInput
from app.store import Store


def test_retention_prunes_only_ephemeral_records(tmp_path):
    store = Store(f'sqlite:///{tmp_path / "retention.db"}')
    store.put('session:expired', 'session', {'turns': []}, expires=time.time() - 10)
    store.put('source:permanent', 'source', {'title': 'Keep'}, permanent=True)
    assert store.get('session:expired') is None
    store.prune()
    assert store.list('session') == []
    assert store.get('source:permanent') == {'title': 'Keep'}


def test_cloud_requires_persistent_db():
    with pytest.raises(ValueError, match='persistent PostgreSQL'):
        Settings(_env_file=None, environment='cloud', database_url='sqlite:///:memory:')


def test_supabase_requires_server_controlled_role(client, app, monkeypatch):
    settings = app.state.service.graph.settings
    settings.supabase_url = 'https://example.supabase.co'
    settings.supabase_anon_key = 'public'

    def auth(user):
        monkeypatch.setattr(httpx, 'get', lambda url, **kwargs: httpx.Response(200,
            request=httpx.Request('GET', url), json=user))

    auth({'id': 'u1', 'user_metadata': {'role': 'mediagent_staff', 'hospital_id': 'cmc-vellore'}})
    headers = {'Authorization': 'Bearer arbitrary-token'}
    assert client.get('/api/staff/sources', headers=headers).status_code == 403
    auth({'id': 'u1', 'app_metadata': {'role': 'mediagent_staff', 'hospital_id': 'wrong-hospital'}})
    assert client.get('/api/staff/sources', headers=headers).status_code == 403
    auth({'id': 'u1', 'app_metadata': {'role': 'mediagent_staff', 'hospital_id': 'cmc-vellore'}})
    assert client.get('/api/staff/sources', headers=headers).status_code == 200


def test_hybrid_failure_falls_back_and_never_uses_unapproved_id(app):
    knowledge = app.state.knowledge
    knowledge.settings.hybrid_enabled = True

    class BrokenIndex:
        def search(self, *args):
            raise ConnectionError('offline')

    knowledge._hybrid = BrokenIndex()
    hits, mode = knowledge.search('book appointment', 'hospital')
    assert hits and mode == 'lexical'

    class StaleIndex:
        def search(self, *args):
            return ['source:not-approved']

    knowledge._hybrid = StaleIndex()
    hits, mode = knowledge.search('book appointment', 'hospital')
    assert hits and all(h['id'] != 'source:not-approved' for h in hits)


def test_document_reimport_is_idempotent(app):
    source = SourceInput(title='Test source for parking', url='https://www.cmcvellore.ac.in/parking',
        text='Please ask campus reception for parking information.', checked_at='2026-10-07')
    knowledge = app.state.knowledge
    first = knowledge.save(source, 'test')
    second = knowledge.save(source, 'test')
    assert first == second
    assert app.state.store.get(first)['revision'] == 1


def test_other_hospital_does_not_receive_cmc_seed(settings):
    from app.main import create_app
    settings.hospital_id = 'another-hospital'
    settings.hospital_name = 'Another Hospital'
    app = create_app(settings)
    assert app.state.knowledge.sources() == []
    assert settings.portal_url == ''


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'https://user:pass@example.org', 'https://example.org/path'])
def test_invalid_public_origin_rejected(url):
    with pytest.raises(ValueError):
        Settings(_env_file=None, public_url=url)


def test_configurable_hospital_portal():
    settings = Settings(_env_file=None, hospital_id='other', hospital_portal_url='https://hospital.example/patients')
    assert settings.portal_url == 'https://hospital.example/patients'


def test_citations_preserve_complete_sentences():
    from app.knowledge import citation
    source = {'title': 'Example', 'url': 'https://hospital.example/info', 'checked_at': '2026-10-07',
              'text': 'First complete sentence. ' + ' '.join(['word'] * 100) + '.'}
    excerpt = citation(source, 1).excerpt
    assert excerpt == 'First complete sentence. Read the linked source for full context.'
