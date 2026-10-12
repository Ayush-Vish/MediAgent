import hashlib
import json
import zipfile

import pytest

from app.config import Settings
from app.knowledge import Knowledge
from app.store import Store
from scripts.ingest_openfda import ingest_archive, record_sources


@pytest.fixture
def knowledge():
    settings = Settings(_env_file=None, environment='test', database_url='sqlite:///:memory:')
    store = Store(settings.database_url)
    yield Knowledge(store, settings)
    store.engine.dispose()


def test_label_sections_keep_identity_and_chunk_long_text():
    record = {'openfda': {'generic_name': ['Metformin']},
              'warnings': ['Public educational warning. ' * 200],
              'warnings_table': ['DUPLICATE TABLE'], 'id': 'record-id'}
    sources = list(record_sources(record, 'label', 'a' * 64, 1,
                                  'https://download.open.fda.gov/drug/label/test.json.zip', '2026-10-09'))
    warnings = [s for s in sources if 'warnings' in s.title]
    assert len(warnings) > 1
    assert all('Metformin' in s.text and '#record-1' in str(s.url) for s in sources)
    assert all(s.category == 'health' for s in sources)
    assert any('DUPLICATE TABLE' in s.text for s in sources)
    assert any('openfda' in s.title for s in sources)
    assert len({s.title for s in sources}) == len(sources)


def test_verified_archive_import_is_durable_pending_and_resumable(tmp_path, knowledge):
    path = tmp_path / 'test.json.zip'
    url = 'https://download.open.fda.gov/drug/ndc/test.json.zip'
    payload = {'results': [{'generic_name': 'Metformin', 'product_ndc': '123-456'},
                           {'generic_name': 'Ibuprofen', 'product_ndc': '123-789'}]}
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('test.json', json.dumps(payload))
    receipt = {'url': url, 'bytes': path.stat().st_size,
               'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    path.with_suffix('.zip.json').write_text(json.dumps(receipt))
    item = {'file': url, 'records': 2}
    result = ingest_archive(knowledge, item, path, 'ndc', '2026-10-09')
    sources = knowledge.sources()
    assert result['records'] == 2 and result['chunks'] == 2
    assert len(sources) == 2 and all(not s['approved'] for s in sources)
    assert not knowledge.active('health')
    assert ingest_archive(knowledge, item, path, 'ndc', '2026-10-09') == result
    assert knowledge.sources() == sources
    assert all(row['expires'] is None for row in knowledge.store.engine.connect().execute(
        knowledge.store.records.select()).mappings())


def test_unverified_archive_rejected_before_writes(tmp_path, knowledge):
    with pytest.raises(ValueError, match='unverified archive'):
        ingest_archive(knowledge, {'file': 'https://download.open.fda.gov/test.zip', 'records': 1},
                       tmp_path / 'missing.zip', 'ndc', '2026-10-09')
    assert not knowledge.sources()


def test_bulk_save_preserves_approval_and_increments_only_changed_sources(knowledge):
    sources = list(record_sources({'generic_name': 'Metformin'}, 'ndc', 'a' * 64, 1,
                                  'https://download.open.fda.gov/test.zip', '2026-10-09'))
    keys = knowledge.save_many(sources, actor='reviewer', approved=True)
    original = knowledge.sources()[0]
    assert knowledge.save_many(sources, actor='importer') == keys
    assert knowledge.sources() == [original]
    sources[0] = sources[0].model_copy(update={'text': sources[0].text + ' Changed public information.'})
    knowledge.save_many(sources, actor='importer')
    updated = knowledge.sources()[0]
    assert not updated['approved'] and updated['revision'] == original['revision'] + 1


def test_medical_preamble_before_questions_is_not_lost():
    warning = 'Important contraindication before questions. ' * 50
    warning += '\nQ: What should a reader know?\nA: This is public regulatory information.'
    sources = list(record_sources({'information_for_patients': [warning]}, 'label', 'b' * 64, 1,
                                  'https://download.open.fda.gov/test.zip', '2026-10-09'))
    assert any('Important contraindication before questions.' in source.text for source in sources)
    assert any('This is public regulatory information.' in source.text for source in sources)


def test_pending_corpus_is_filtered_by_database_before_loading(knowledge, monkeypatch):
    sources = list(record_sources({'generic_name': 'Metformin'}, 'ndc', 'a' * 64, 1,
                                  'https://download.open.fda.gov/test.zip', '2026-10-09'))
    knowledge.save_many(sources, actor='importer')
    monkeypatch.setattr(knowledge.store, 'list', lambda _: pytest.fail('Full pending corpus loaded'))
    assert knowledge.active('health') == []
