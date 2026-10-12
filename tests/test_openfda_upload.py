import json
import zipfile
from types import SimpleNamespace

import pytest

from app.knowledge import source_key
from scripts import upload_openfda as uploader
from scripts.download_openfda import digest
from scripts.ingest_openfda import record_sources


def test_prepare_preserves_local_recursive_chunk_ids_and_text(tmp_path):
    url = 'https://download.open.fda.gov/drug/label/test.json.zip'
    record = {'id': 'label-id', 'set_id': 'set-id',
              'openfda': {'generic_name': ['Metformin'], 'brand_name': ['Example'],
                          'route': ['ORAL'], 'product_type': ['HUMAN PRESCRIPTION DRUG']},
              'indications_and_usage': ['General regulatory medicine information. ' * 60],
              'warnings': ['Public warnings about this medicine.'], 'version': '2'}
    path = tmp_path / 'drug/label/2026-10-09/test.json.zip'
    path.parent.mkdir(parents=True)
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('test.json', json.dumps({'results': [record, record]}))
    sha = digest(path)
    path.with_suffix('.zip.json').write_text(json.dumps({'url': url, 'sha256': sha, 'bytes': path.stat().st_size}))
    (tmp_path / 'download.json').write_text(json.dumps({'results': {'drug': {'label': {
        'export_date': '2026-10-09', 'partitions': [{'file': url}]}}}}))
    output = tmp_path / 'selected.jsonl'
    uploader.prepare(tmp_path, output, 'cmc-vellore')
    actual = [json.loads(line) for line in output.read_text().splitlines()]
    expected = [s for s in record_sources(record, 'label', sha, 1, url, '2026-10-09')
                if s.title.rsplit(' — ', 2)[-2].replace(' ', '_') in uploader.SECTIONS]
    assert len(actual) == len(expected)  # Duplicate label signature was skipped.
    assert [row['text'] for row in actual] == [s.text for s in expected]
    assert [row['id'] for row in actual] == [source_key(s.model_dump(mode='json'), 'cmc-vellore') for s in expected]
    assert all(not row['approved'] for row in actual)


def test_failed_remote_batch_resumes_without_skipping_unacknowledged_sources(tmp_path):
    prepared = tmp_path / 'points.jsonl'
    prepared.write_text(''.join(json.dumps({'id': str(i), 'title': 'FDA — warnings — chunk 1', 'text': 'FDA record\nFDA export date\nPublic medicine information.'}) + '\n' for i in range(130)))
    checkpoint = tmp_path / 'checkpoint.json'
    settings = SimpleNamespace(qdrant_url='https://example.cloud.qdrant.io', qdrant_collection='mediagent')
    received = []
    class Index:
        def __init__(self, fail=False):
            self.settings = settings
            self.fail = fail
        def index(self, batch, pre_chunked):
            if self.fail and len(received) == 64:
                raise RuntimeError('Transport interrupted')
            received.extend(row['id'] for row in batch)
    with pytest.raises(RuntimeError):
        uploader.upload(Index(fail=True), prepared, checkpoint)
    assert json.loads(checkpoint.read_text())['acknowledged'] == 64
    assert uploader.upload(Index(), prepared, checkpoint) == 130
    assert received == [str(i) for i in range(130)]
