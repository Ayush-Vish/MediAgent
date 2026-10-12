"""Download official openFDA bulk ZIPs without extracting or publishing them.

The saved manifest pins an export snapshot. Failed transfers resume on rerun;
completed archives are checked against locally recorded SHA-256 digests.
"""
import argparse
import hashlib
import json
import re
import shutil
import time
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from app.config import ROOT

MANIFEST_URL = 'https://api.fda.gov/download.json'
MEDICINE_ENDPOINTS = {'label', 'ndc', 'drugsfda', 'orangebook', 'enforcement', 'shortages'}
RESERVE = 1024 ** 3


def partitions(manifest, scope):
    for category, endpoints in manifest['results'].items():
        for endpoint, data in endpoints.items():
            if scope == 'medicine' and (category != 'drug' or endpoint not in MEDICINE_ENDPOINTS):
                continue
            if (not re.fullmatch(r'[a-z0-9]+', category) or not re.fullmatch(r'[a-z0-9]+', endpoint)
                    or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', data['export_date'])):
                raise ValueError('Unexpected openFDA dataset path')
            prefix = f'/{category}/{endpoint}/'
            for item in data['partitions']:
                parsed = urlsplit(item['file'])
                if (parsed.scheme != 'https' or parsed.netloc != 'download.open.fda.gov'
                        or parsed.query or parsed.fragment or not parsed.path.startswith(prefix)
                        or not parsed.path.endswith('.json.zip')
                        or any(part in {'', '.', '..'} for part in parsed.path[1:].split('/'))):
                    raise ValueError('Unexpected openFDA archive URL')
                yield item, Path(category) / endpoint / data['export_date'] / parsed.path.removeprefix(prefix)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def complete(target, url):
    receipt = target.with_suffix(target.suffix + '.json')
    if not target.exists() or not receipt.exists():
        return False
    info = json.loads(receipt.read_text())
    return (info.get('url') == url and target.stat().st_size == info.get('bytes')
            and digest(target) == info.get('sha256'))


def download(client, url, target):
    if complete(target, url):
        return 'verified'
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + '.part')
    state_path = partial.with_suffix(partial.suffix + '.json')
    for attempt in range(3):
        try:
            state = json.loads(state_path.read_text()) if state_path.exists() else {}
            offset = partial.stat().st_size if partial.exists() and state.get('etag') and state.get('url') == url else 0
            headers = {'Accept-Encoding': 'identity'}
            if offset:
                headers.update({'Range': f'bytes={offset}-', 'If-Range': state['etag']})
            with client.stream('GET', url, headers=headers) as response:
                if response.status_code == 416 and offset:
                    state_path.unlink(missing_ok=True)  # Restart a stale or fully transferred partial.
                response.raise_for_status()
                if response.status_code == 206:
                    content_range = response.headers.get('content-range', '')
                    if not offset or not content_range.startswith(f'bytes {offset}-'):
                        raise ValueError('Invalid resume response')
                elif response.status_code == 200:
                    offset = 0
                else:
                    raise ValueError(f'Unexpected download status {response.status_code}')
                write_json(state_path, {'url': url, 'etag': response.headers.get('etag')})
                length = response.headers.get('content-length')
                expected = offset + int(length) if length else None
                with partial.open('ab' if offset else 'wb') as stream:
                    for block in response.iter_bytes(chunk_size=1024 * 1024):
                        if shutil.disk_usage(target.parent).free < len(block) + RESERVE:
                            raise OSError('Insufficient disk space; partial archive retained')
                        stream.write(block)
                if expected is not None and partial.stat().st_size != expected:
                    raise ValueError('Incomplete archive; rerun to resume')
            with zipfile.ZipFile(partial) as archive:
                bad = archive.testzip()
                if bad:
                    raise ValueError(f'ZIP checksum failed: {bad}')
            info = {'url': url, 'bytes': partial.stat().st_size, 'sha256': digest(partial)}
            partial.replace(target)
            write_json(target.with_suffix(target.suffix + '.json'), info)
            state_path.unlink(missing_ok=True)
            return 'downloaded'
        except (httpx.TransportError, httpx.HTTPStatusError):
            if attempt == 2:
                raise
            time.sleep(attempt + 1)
    raise RuntimeError('Download failed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scope', choices=['all', 'medicine'], default='all')
    parser.add_argument('--output', type=Path, default=ROOT / 'data/raw/openfda')
    parser.add_argument('--plan', action='store_true', help='Show size and archive count without downloading ZIPs')
    parser.add_argument('--refresh', action='store_true', help='Fetch a newer export manifest')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output / 'download.json'
    with httpx.Client(timeout=60, follow_redirects=False,
                      headers={'User-Agent': 'MediAgent/1.0 (public data download)'}) as client:
        if args.refresh or not manifest_path.exists():
            response = client.get(MANIFEST_URL)
            response.raise_for_status()
            manifest = response.json()
            list(partitions(manifest, 'all'))  # Validate links before persisting the manifest.
            write_json(manifest_path, manifest)
        else:
            manifest = json.loads(manifest_path.read_text())
        selected = list(partitions(manifest, args.scope))
        estimate = sum(float(item['size_mb']) * 1024 ** 2 for item, _ in selected)
        print(f'openfda.plan scope={args.scope} archives={len(selected)} '
              f'estimated_gib={estimate / 1024 ** 3:.2f} snapshot={manifest["meta"]["last_updated"]}', flush=True)
        if args.plan:
            return
        remaining = sum(float(item['size_mb']) * 1024 ** 2 for item, path in selected
                        if not complete(args.output / path, item['file']))
        if shutil.disk_usage(args.output).free < remaining + RESERVE:
            raise SystemExit('Not enough disk space for this scope plus 1 GiB reserve. Choose a larger --output path.')
        for index, (item, path) in enumerate(selected, 1):
            target = args.output / path
            print(f'openfda.download.started progress={index}/{len(selected)} url={item["file"]}', flush=True)
            status = download(client, item['file'], target)
            print(f'openfda.download.{status} bytes={target.stat().st_size} path={target}', flush=True)
        print(f'Completed {len(selected)} verified archives. Raw data only; nothing published to chat or Qdrant.', flush=True)


if __name__ == '__main__':
    main()
