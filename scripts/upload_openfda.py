"""Upload a bounded FDA medicine-label corpus to Qdrant after recursive chunking.

Prepared sources retain their existing local database IDs. Uploading a pending
source does not approve it for patient-facing retrieval. Resume checkpoints are
written only after Qdrant acknowledges each batch.
"""
import argparse
import hashlib
import json
import os
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

import ijson
from qdrant_client import QdrantClient, models

from app.config import ROOT, Settings
from app.knowledge import source_key
from app.vector import DENSE_MODEL, SPARSE_MODEL, HybridIndex
from scripts.download_openfda import complete, partitions, write_json
from scripts.ingest_openfda import record_sources

LIMIT = 50_000  # Conservative initial corpus for the user's 1 GB RAM / 5 GB disk cluster.
SECTIONS = {'indications_and_usage', 'purpose', 'uses', 'contraindications', 'warnings',
            'warnings_and_cautions', 'drug_interactions', 'adverse_reactions', 'when_using',
            'ask_doctor', 'ask_doctor_or_pharmacist', 'stop_use', 'pregnancy_or_breast_feeding',
            'pregnancy', 'information_for_patients', 'patient_medication_information',
            'active_ingredient', 'inactive_ingredient'}


def prepare(root, output, hospital_id):
    if output.exists():
        return
    manifest = json.loads((root / 'download.json').read_text())
    selected = [(item, path) for item, path in partitions(manifest, 'medicine') if path.parts[1] == 'label']
    temporary = output.with_suffix('.part')
    seen = set()
    count = 0
    output.parent.mkdir(parents=True, exist_ok=True)
    with temporary.open('w', encoding='utf-8') as destination:
        for item, relative in selected:
            path = root / relative
            if not complete(path, item['file']):
                raise ValueError(f'Unverified FDA archive: {relative}')
            sha = json.loads(path.with_suffix(path.suffix + '.json').read_text())['sha256']
            with zipfile.ZipFile(path) as archive:
                members = [m for m in archive.infolist() if m.filename.endswith('.json')]
                if len(members) != 1:
                    raise ValueError('Expected one FDA JSON member')
                with archive.open(members[0]) as stream:
                    for number, record in enumerate(ijson.items(stream, 'results.item', use_float=True), 1):
                        metadata = record.get('openfda', {})
                        if not any(t.startswith('HUMAN') for t in metadata.get('product_type', [])):
                            continue
                        names = metadata.get('generic_name', [])
                        if not names:
                            continue
                        signature = tuple(tuple(sorted(str(v).casefold() for v in metadata.get(k, [])))
                                          for k in ('generic_name', 'brand_name', 'route'))
                        if signature in seen:
                            continue
                        seen.add(signature)
                        # Retain complete selected sections, rather than cutting medical sentences.
                        for source in record_sources(record, 'label', sha, number, item['file'], relative.parts[2]):
                            section = source.title.rsplit(' — ', 2)[-2].replace(' ', '_')
                            if section not in SECTIONS:
                                continue
                            # Context and stable titles are identical to the full local import.
                            data = source.model_dump(mode='json')
                            data.update(id=source_key(data, hospital_id), hospital_id=hospital_id, approved=False)
                            destination.write(json.dumps(data) + '\n')
                            count += 1
                            if count >= LIMIT:
                                break
                        if count >= LIMIT:
                            break
            print(f'openfda.prepare points={count} medicine_labels={len(seen)} archive={relative}', flush=True)
            if count >= LIMIT:
                break
    if not count:
        raise ValueError('No human medicine label text found')
    temporary.replace(output)
    print(f'openfda.prepare.completed points={count} medicine_labels={len(seen)}', flush=True)


def upload(index, prepared, checkpoint_path):
    with prepared.open('rb') as stream:
        sha = hashlib.file_digest(stream, 'sha256').hexdigest()
    target = {'host': urlsplit(index.settings.qdrant_url).hostname,
              'collection': index.settings.qdrant_collection, 'sha256': sha,
              'dense_model': DENSE_MODEL, 'sparse_model': SPARSE_MODEL}
    checkpoint = json.loads(checkpoint_path.read_text()) if checkpoint_path.exists() else {}
    if checkpoint and checkpoint.get('target') != target:
        raise ValueError('Upload checkpoint belongs to a different corpus or target')
    acknowledged = checkpoint.get('acknowledged', 0)
    batch = []
    with prepared.open(encoding='utf-8') as stream:
        for position, line in enumerate(stream, 1):
            if position <= acknowledged:
                continue
            source = json.loads(line)
            # Embed medical meaning, keeping regulatory IDs/dates in the full payload.
            section = source['title'].rsplit(' — ', 2)[-2]
            clinical_lines = [value for value in source['text'].splitlines()[2:]
                              if not value.startswith(('id:', 'set id:'))]
            source['embedding_text'] = section + '\n' + '\n'.join(clinical_lines)
            batch.append(source)
            if len(batch) == 64:
                index.index(batch, pre_chunked=True)
                acknowledged = position
                write_json(checkpoint_path, {'target': target, 'acknowledged': acknowledged})
                batch.clear()
                print(f'openfda.upload.acknowledged points={acknowledged}', flush=True)
    if batch:
        index.index(batch, pre_chunked=True)
        acknowledged += len(batch)
        write_json(checkpoint_path, {'target': target, 'acknowledged': acknowledged})
    return acknowledged


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, default=ROOT / 'MediAgent.env')
    args = parser.parse_args()
    settings = Settings(_env_file=args.env_file, environment='local', database_url='sqlite:///runtime/mediagent.db')
    if not settings.qdrant_url or not settings.qdrant_api_key:
        raise SystemExit('Configure cloud QDRANT_URL and QDRANT_API_KEY in the env file.')
    prepared = ROOT / 'runtime/openfda-qdrant.jsonl'
    prepare(ROOT / 'data/raw/openfda', prepared, settings.hospital_id)
    # Configure remote storage before loading local embedding models.
    client = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key, timeout=60)
    try:
        info = client.get_collection(settings.qdrant_collection)
        vectors = info.config.params.vectors
        if not isinstance(vectors, dict) or 'dense' not in vectors or vectors['dense'].size != 384:
            raise ValueError('Existing collection must have a named 384-dimensional dense vector')
        client.update_collection(settings.qdrant_collection,
                                 vectors_config={'dense': models.VectorParamsDiff(on_disk=True)},
                                 hnsw_config=models.HnswConfigDiff(on_disk=True),
                                 collection_params=models.CollectionParamsDiff(on_disk_payload=True),
                                 sparse_vectors_config={'sparse': models.SparseVectorParams(
                                     index=models.SparseIndexParams(on_disk=True), modifier=models.Modifier.IDF)})
        index = HybridIndex(settings.model_copy(update={'rerank_enabled': False}), client=client,
                            embedding_threads=min(8, os.cpu_count() or 1))
        count = upload(index, prepared, ROOT / 'runtime/openfda-qdrant-checkpoint.json')
        print(f'Uploaded {count} recursively chunked FDA medicine points to {settings.qdrant_collection}. '
              'Sources remain pending clinical review.', flush=True)
    finally:
        client.close()


if __name__ == '__main__':
    main()
