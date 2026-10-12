import io
import json
import zipfile

import httpx
import pytest

from scripts.download_openfda import complete, download, partitions

URL = 'https://download.open.fda.gov/drug/label/drug-label-0001-of-0001.json.zip'


def archive_bytes():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        archive.writestr('data.json', '{"results": []}')
    return stream.getvalue()


def manifest():
    return {'results': {'drug': {
        'label': {'export_date': '2026-10-09', 'partitions': [{'file': URL}]},
        'event': {'export_date': '2026-10-09', 'partitions': [
            {'file': 'https://download.open.fda.gov/drug/event/2024q1/drug-event-0001-of-0001.json.zip'},
            {'file': 'https://download.open.fda.gov/drug/event/2024q2/drug-event-0001-of-0001.json.zip'},
        ]},
    }}}


def test_scope_excludes_adverse_events_and_preserves_quarter_paths():
    selected = list(partitions(manifest(), 'medicine'))
    assert len(selected) == 1
    assert str(selected[0][1]) == 'drug/label/2026-10-09/drug-label-0001-of-0001.json.zip'
    paths = [str(path) for _, path in partitions(manifest(), 'all')]
    assert len(set(paths)) == 3
    assert any('/2024q1/' in path for path in paths)


@pytest.mark.parametrize('url', ['https://evil.example/file.json.zip',
                                 'https://download.open.fda.gov/drug/label/../file.json.zip',
                                 'http://download.open.fda.gov/drug/label/file.json.zip'])
def test_manifest_rejects_unsafe_urls(url):
    data = manifest()
    data['results']['drug']['label']['partitions'][0]['file'] = url
    with pytest.raises(ValueError):
        list(partitions(data, 'medicine'))


def test_download_verifies_and_skips_completed_archive(tmp_path):
    calls = []
    payload = archive_bytes()
    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=payload, headers={'etag': '"v1"'})
    target = tmp_path / 'archive.zip'
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert download(client, URL, target) == 'downloaded'
        assert download(client, URL, target) == 'verified'
    assert len(calls) == 1
    assert complete(target, URL)
    target.write_bytes(b'corrupted')
    assert not complete(target, URL)


@pytest.mark.parametrize('server_resumes', [True, False])
def test_resume_and_server_ignoring_range(tmp_path, server_resumes):
    payload = archive_bytes()
    target = tmp_path / 'archive.zip'
    partial = tmp_path / 'archive.zip.part'
    partial.write_bytes(payload[:20])
    (tmp_path / 'archive.zip.part.json').write_text(json.dumps({'url': URL, 'etag': '"v1"'}))
    def handler(request):
        assert request.headers['range'] == 'bytes=20-'
        assert request.headers['if-range'] == '"v1"'
        if server_resumes:
            return httpx.Response(206, content=payload[20:], headers={
                'etag': '"v1"', 'content-range': f'bytes 20-{len(payload)-1}/{len(payload)}'})
        return httpx.Response(200, content=payload, headers={'etag': '"v2"'})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        download(client, URL, target)
    assert target.read_bytes() == payload


def test_invalid_resume_does_not_publish_archive(tmp_path):
    target = tmp_path / 'archive.zip'
    (tmp_path / 'archive.zip.part').write_bytes(b'partial')
    (tmp_path / 'archive.zip.part.json').write_text(json.dumps({'url': URL, 'etag': '"v1"'}))
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
            206, content=b'wrong', headers={'content-range': 'bytes 0-4/5'}))) as client:
        with pytest.raises(ValueError, match='Invalid resume'):
            download(client, URL, target)
    assert not target.exists()


def test_stale_partial_restarts_when_server_rejects_range(tmp_path, monkeypatch):
    monkeypatch.setattr('scripts.download_openfda.time.sleep', lambda _: None)
    target = tmp_path / 'archive.zip'
    (tmp_path / 'archive.zip.part').write_bytes(b'stale partial')
    (tmp_path / 'archive.zip.part.json').write_text(json.dumps({'url': URL, 'etag': '"v1"'}))
    calls = []
    def handler(request):
        calls.append(request)
        if 'range' in request.headers:
            return httpx.Response(416)
        return httpx.Response(200, content=archive_bytes())
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        download(client, URL, target)
    assert len(calls) == 2
    assert complete(target, URL)


def test_invalid_zip_is_not_published(tmp_path):
    target = tmp_path / 'archive.zip'
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
            200, content=b'not a zip archive'))) as client:
        with pytest.raises(zipfile.BadZipFile):
            download(client, URL, target)
    assert not target.exists()
    assert not complete(target, URL)
