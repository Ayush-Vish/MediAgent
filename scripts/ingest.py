"""Fetch allowlisted public HTML or parse a local public PDF with Docling.

All imported chunks remain pending until a staff member reviews them.
Never pass patient records. PDFs are parsed locally, not on the hosted API.
"""
import argparse
import hashlib
import json
import io
import re
import zipfile
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup

from app.config import ROOT, Settings
from app.knowledge import Knowledge
from app.schemas import SourceInput
from app.store import Store

MANIFEST = [
    ('CMC patient portal', 'https://www.cmcvellore.ac.in/patient-portal/'),
    ('CMC patient FAQs', 'https://www.cmcvellore.edu.in/faqs/'),
    ('CMC insurance ACCESS', 'https://www.cmcvellore.ac.in/insurance-access/'),
]
ALLOWED_HOSTS = {'www.cmcvellore.ac.in', 'www.cmcvellore.edu.in', 'www.nhs.uk'}


def fetch_html(url):
    for _ in range(4):
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or parsed.hostname not in ALLOWED_HOSTS or parsed.port not in (None, 443) or parsed.username:
            raise ValueError('Only allowlisted official HTTPS websites can be fetched')
        with httpx.stream('GET', url, timeout=30, follow_redirects=False,
                          headers={'User-Agent': 'MediAgentDemo/1.0 (public patient information)'}) as response:
            if response.is_redirect:
                from urllib.parse import urljoin
                url = urljoin(url, response.headers['location'])
                continue
            response.raise_for_status()
            if 'text/html' not in response.headers.get('content-type', ''):
                raise ValueError('Expected an HTML page; use --file for PDF documents')
            content = bytearray()
            for block in response.iter_bytes():
                content.extend(block)
                if len(content) > 5_000_000:
                    raise ValueError('Source exceeds 5 MB')
            return url, bytes(content)
    raise ValueError('Too many redirects')


from app.chunker import recursive_chunk_text


def html_text(content):
    soup = BeautifulSoup(content, 'html.parser')
    for item in soup(['script', 'style', 'nav', 'footer', 'header', 'form', 'noscript']):
        item.decompose()
    main = soup.find('main') or soup.find('article') or soup.body or soup
    return main.get_text('\n', strip=True)


def chunks(text, size=180):
    for _, chunk_text, _ in recursive_chunk_text(text, section_title="General", chunk_size=size, overlap=30):
        yield chunk_text


def pdf_pages(path):
    from docling.document_converter import DocumentConverter
    document = DocumentConverter().convert(path).document
    pages = {}
    for item, _ in document.iterate_items():
        if not getattr(item, 'text', None):
            continue
        provenance = getattr(item, 'prov', [])
        page = provenance[0].page_no if provenance else 1
        pages.setdefault(page, []).append(item.text)
    for page, blocks in sorted(pages.items()):
        yield page, '\n'.join(blocks)


def medlineplus_topics():
    """Download the current public MedlinePlus health-topic XML and yield sources.

    MedlinePlus publishes this file daily (Tuesday-Saturday). The summaries are
    patient education, not prescribing instructions; every imported record stays
    pending until a hospital staff member approves it.
    """
    page = httpx.get('https://medlineplus.gov/xml.html', timeout=30).text
    links = re.findall(r'href="([^"]+mplus_topics_compressed_[^"]+\.zip)"', page)
    if not links:
        raise RuntimeError('MedlinePlus XML download link was not found')
    url = links[0]
    response = httpx.get(url, timeout=60)
    response.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        xml_name = next(name for name in archive.namelist() if name.endswith('.xml'))
        root = ET.fromstring(archive.read(xml_name))
    for topic in root.findall('health-topic'):
        if topic.attrib.get('language') != 'English':
            continue
        title = topic.attrib.get('title', '').strip()
        topic_url = topic.attrib.get('url', '').strip()
        summary = topic.findtext('full-summary', default='').strip()
        if not title or not topic_url or not summary:
            continue
        summary = BeautifulSoup(summary, 'html.parser').get_text(' ', strip=True)
        aliases = [item.text.strip() for item in topic.findall('also-called') if item.text and item.text.strip()]
        text = f'{title}. Also called: {", ".join(aliases)}. {summary}' if aliases else f'{title}. {summary}'
        yield title[:180], topic_url, text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--fetch-cmc', action='store_true')
    group.add_argument('--fetch-medlineplus', action='store_true',
                       help='Import the current MedlinePlus public health-topic dataset')
    group.add_argument('--url')
    group.add_argument('--file', type=Path)
    parser.add_argument('--title')
    parser.add_argument('--source-url')
    parser.add_argument('--category', choices=['hospital', 'health'], default='hospital')
    parser.add_argument('--checked-at', type=date.fromisoformat, default=date.today())
    args = parser.parse_args()
    settings = Settings()
    store = Store(settings.database_url, settings.retention_hours)
    knowledge = Knowledge(store, settings)
    sources = []
    if args.fetch_medlineplus:
        count = 0
        for title, url, text in medlineplus_topics():
            for index, piece in enumerate(chunks(text), 1):
                name = f'MedlinePlus — {title}' + (f' — chunk {index}' if index > 1 else '')
                knowledge.save(SourceInput(title=name, url=url, text=piece,
                                           category='health', checked_at=args.checked_at),
                               actor='MedlinePlus bulk ingestion')
                count += 1
        print(f'Imported {count} MedlinePlus health-topic records/chunks as pending. Review and approve them in /staff.')
        return
    if args.file:
        if not args.title or not args.source_url or args.file.suffix.lower() != '.pdf':
            parser.error('--file requires a PDF, --title and --source-url')
        sources = [(args.title, args.source_url, list(pdf_pages(args.file.resolve(strict=True))))]
    else:
        if args.url and not args.title:
            parser.error('--url requires --title')
        for title, requested_url in (MANIFEST if args.fetch_cmc else [(args.title, args.url)]):
            url, content = fetch_html(requested_url)
            raw = ROOT / 'data/raw'
            raw.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256(content).hexdigest()
            (raw / f'{digest}.html').write_bytes(content)
            (raw / f'{digest}.json').write_text(json.dumps({'url': url, 'fetched_at': str(date.today()), 'sha256': digest}), encoding='utf-8')
            sources.append((title, url, [(None, html_text(content))]))
    count = 0
    for title, url, pages in sources:
        active_ids = set()
        prefix = title + ' — '
        for page, text in pages:
            for index, piece in enumerate(chunks(text), 1):
                name = f'{prefix}page {page or "web"}, chunk {index}'
                key = knowledge.save(SourceInput(title=name, url=url, text=piece, category=args.category,
                                                 checked_at=args.checked_at, page=page), actor='offline ingestion')
                active_ids.add(key)
                count += 1
        # Retire removed chunks from a previous import; preserve unrelated curated sources.
        for old in knowledge.sources():
            if old['url'] == url and old['title'].startswith(prefix) and old['id'] not in active_ids:
                old['approved'] = False
                old['conflict'] = True
                store.put(old['id'], 'source', old, permanent=True)
    print(f'Imported {count} chunks. Review and approve them in /staff before they can answer questions.')


if __name__ == '__main__':
    main()
