from datetime import date

import pytest

from app.config import Settings
from app.knowledge import Knowledge, topic_for
from app.store import Store
from scripts import ingest


@pytest.fixture
def knowledge():
    settings = Settings(_env_file=None, database_url='sqlite:///:memory:', environment='test')
    store = Store(settings.database_url)
    yield Knowledge(store, settings)
    store.engine.dispose()


def test_medicine_links_stay_within_selected_official_medicine():
    html = b'''<a href="side-effects/">Good</a>
    <a href="side-effects/#serious">Duplicate</a>
    <a href="https://evil.example/medicines/metformin/foo/">External</a>
    <a href="../amoxicillin/">Other medicine</a>
    <a href="//www.nhs.uk:444/medicines/metformin/foo/">Wrong port</a>
    <a href="foo/?search=test">Query</a>
    <a href="foo/deeper/">Nested</a>'''
    assert ingest.medicine_links(html, 'https://www.nhs.uk/medicines/metformin/') == [
        'https://www.nhs.uk/medicines/metformin/side-effects/']


def test_reimport_preserves_ids_and_approval_but_changed_text_requires_review(knowledge):
    args = (knowledge, 'NHS metformin', 'https://www.nhs.uk/medicines/metformin/')
    text = 'Metformin is a medicine used to treat type 2 diabetes. Public patient education.'
    ingest.import_pages(*args, [(None, text)], 'health', date.today())
    source = knowledge.sources()[0]
    assert not source['approved']
    assert not knowledge.active('health')
    source['approved'] = True
    knowledge.store.put(source['id'], 'source', source, permanent=True)
    ingest.import_pages(*args, [(None, text)], 'health', date.today())
    assert knowledge.sources() == [source]
    ingest.import_pages(*args, [(None, text + ' Updated public information.')], 'health', date.today())
    updated = knowledge.sources()[0]
    assert updated['id'] == source['id']
    assert updated['revision'] == source['revision'] + 1
    assert not updated['approved']


def test_removed_chunks_retired_but_empty_input_preserves_previous(knowledge):
    args = (knowledge, 'NHS metformin', 'https://www.nhs.uk/medicines/metformin/')
    text = 'Metformin is used for diabetes. ' * 150
    ingest.import_pages(*args, [(None, text)], 'health', date.today())
    assert len(knowledge.sources()) > 1
    with pytest.raises(ValueError, match='No usable text'):
        ingest.import_pages(*args, [], 'health', date.today())
    assert all(not source['conflict'] for source in knowledge.sources())
    ingest.import_pages(*args, [(None, 'Metformin is used for type 2 diabetes. Public patient education.')],
                        'health', date.today())
    sources = knowledge.sources()
    assert sum(not source['conflict'] for source in sources) == 1
    assert all(not source['approved'] for source in sources)


def test_medicine_pipeline_archives_imports_and_logs(monkeypatch, knowledge, tmp_path, capsys):
    root = 'https://www.nhs.uk/medicines/metformin/'
    documents = {
        root: b'<main><h1>Metformin</h1><p>Metformin treats type 2 diabetes.</p><a href="side-effects/">Side effects</a></main>',
        root + 'side-effects/': b'<main><h1>Side effects</h1><p>Read public patient information about metformin side effects.</p></main>',
    }
    monkeypatch.setattr(ingest, 'ROOT', tmp_path)
    monkeypatch.setattr(ingest, 'MEDICINES', ('metformin',))
    monkeypatch.setattr(ingest, 'fetch_html', lambda url: (url, documents[url]))
    ingest.ingest_medicines(knowledge, date.today())
    sources = knowledge.sources()
    assert len(sources) == 2
    assert all(s['category'] == 'health' and not s['approved'] for s in sources)
    assert all('metformin' in s['title'].lower() for s in sources)
    assert len(list((tmp_path / 'data/raw').glob('*.json'))) == 2
    assert 'url=' + root in capsys.readouterr().out
    ids = {source['id'] for source in sources}
    documents[root] = documents[root].replace(b'<h1>Metformin</h1>', b'<h1>Metformin tablets</h1>')
    ingest.ingest_medicines(knowledge, date.today())
    assert {source['id'] for source in knowledge.sources()} == ids


@pytest.mark.parametrize('name', ['ibuprofen', 'amoxicillin', 'metformin', 'omeprazole',
                                 'atorvastatin', 'salbutamol'])
def test_new_medicines_are_recognized_as_medical_topics(name):
    assert topic_for(name) == 'medicine'
