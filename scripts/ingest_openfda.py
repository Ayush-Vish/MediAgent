"""Stream verified openFDA medicine ZIPs into the local source database as pending chunks."""
import argparse
import hashlib
import json
import shutil
import zipfile
from datetime import date
from pathlib import Path

import ijson
from bs4 import BeautifulSoup

from app.chunker import recursive_chunk_text
from app.config import ROOT, Settings
from app.knowledge import Knowledge
from app.schemas import SourceInput
from app.store import Store
from scripts.download_openfda import complete, partitions

BATCH_SIZE = 500
PIPELINE_VERSION = 1


def readable(value, prefix=''):
    """Preserve structured fields and list order in readable labelled lines."""
    if isinstance(value, dict):
        return '\n'.join(readable(v, f'{prefix} {k.replace("_", " ")}'.strip())
                         for k, v in sorted(value.items()))
    if isinstance(value, list):
        return '\n'.join(readable(v, f'{prefix} {i + 1}') for i, v in enumerate(value))
    text = str(value).strip() if value is not None else ''
    if '<' in text and '>' in text:
        soup = BeautifulSoup(text, 'html.parser')
        for item in soup(['script', 'style']):
            item.decompose()
        text = soup.get_text(' ', strip=True)
    return '\n'.join(f'{prefix}: {line}' for line in text.splitlines() if line.strip())


def record_sources(record, endpoint, archive_sha, record_index, url, export_date):
    identity = f'openFDA {endpoint} {archive_sha[:16]} record {record_index}'
    names = record.get('openfda', {})
    context = readable({key: value for key, value in record.items()
                        if key in {'brand_name', 'generic_name', 'product_ndc', 'application_number',
                                   'product_description', 'presentation', 'set_id', 'id'}})
    context += '\n' + readable({key: value for key, value in names.items()
                                if key in {'brand_name', 'generic_name', 'substance_name'}})
    header = f'{identity}\nFDA export date: {export_date}\n{context.strip()[:2000]}\n'
    # Labels retain section boundaries; other datasets retain their full structured record.
    sections = [(key, readable(value)) for key, value in sorted(record.items())] if endpoint == 'label' else [('record', readable(record))]
    for section, text in sections:
        if not text.strip():
            continue
        for index, (_, piece, _) in enumerate(recursive_chunk_text(
                text, section_title=section, chunk_size=180, overlap=30), 1):
            yield SourceInput(title=f'{identity} — {section.replace("_", " ")[:60]} — chunk {index}'[:180],
                              url=f'{url}#record-{record_index}', text=header + piece,
                              category='health', checked_at=date.fromisoformat(export_date))


def ingest_archive(knowledge, item, path, endpoint, export_date):
    if not complete(path, item['file']):
        raise ValueError(f'Missing or unverified archive: {path}; run scripts.download_openfda first')
    receipt = json.loads(path.with_suffix(path.suffix + '.json').read_text())
    sha = receipt['sha256']
    checkpoint_key = 'ingest:openfda:' + hashlib.sha256(
        f'{knowledge.settings.hospital_id}:{PIPELINE_VERSION}:{sha}'.encode()).hexdigest()
    checkpoint = knowledge.store.get(checkpoint_key)
    if checkpoint:
        print(f'openfda.ingest.skipped endpoint={endpoint} records={checkpoint["records"]} chunks={checkpoint["chunks"]}', flush=True)
        return checkpoint
    records = count = 0
    pending = []
    def flush():
        if shutil.disk_usage(Path(knowledge.settings.database_url.removeprefix('sqlite:///')).resolve().parent).free < 1024 ** 3:
            raise OSError('Less than 1 GiB disk space remains; stop and resume on a larger disk')
        knowledge.save_many(pending, actor='openFDA offline ingestion')
        pending.clear()
    with zipfile.ZipFile(path) as archive:
        members = [member for member in archive.infolist() if member.filename.endswith('.json')]
        if len(members) != 1:
            raise ValueError(f'Expected one JSON member in {path}')
        with archive.open(members[0]) as stream:
            for records, record in enumerate(ijson.items(stream, 'results.item', use_float=True), 1):
                for source in record_sources(record, endpoint, sha, records, item['file'], export_date):
                    pending.append(source)
                    count += 1
                    if len(pending) >= BATCH_SIZE:
                        flush()
                if records % 1000 == 0:
                    print(f'openfda.ingest.progress endpoint={endpoint} records={records} chunks={count}', flush=True)
    if pending:
        flush()
    if records != item['records']:
        raise ValueError(f'Record count mismatch for {path}: expected {item["records"]}, read {records}')
    checkpoint = dict(url=item['file'], sha256=sha, export_date=export_date,
                      records=records, chunks=count, pipeline_version=PIPELINE_VERSION)
    knowledge.store.put(checkpoint_key, 'ingestion', checkpoint, permanent=True)
    print(f'openfda.ingest.completed endpoint={endpoint} records={records} chunks={count} status=pending', flush=True)
    return checkpoint


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=ROOT / 'data/raw/openfda')
    args = parser.parse_args()
    settings = Settings()
    if not settings.database_url.startswith('sqlite:///'):
        raise SystemExit('This command is for local SQLite ingestion; configure a local DATABASE_URL.')
    knowledge = Knowledge(Store(settings.database_url, settings.retention_hours), settings)
    manifest = json.loads((args.input / 'download.json').read_text())
    selected = list(partitions(manifest, 'medicine'))
    # Check all archives before any write; do not silently import a partial dataset.
    for item, relative in selected:
        if not complete(args.input / relative, item['file']):
            raise SystemExit(f'Missing or unverified archive: {relative}')
    total_records = total_chunks = 0
    try:
        for item, relative in selected:
            endpoint, export_date = relative.parts[1:3]
            print(f'openfda.ingest.started endpoint={endpoint} archive={relative}', flush=True)
            result = ingest_archive(knowledge, item, args.input / relative, endpoint, export_date)
            total_records += result['records']
            total_chunks += result['chunks']
        print(f'Imported {total_records} FDA records as {total_chunks} permanent pending chunks. '
              'Review in /staff before indexing into Qdrant.', flush=True)
    finally:
        knowledge.store.engine.dispose()


if __name__ == '__main__':
    main()
