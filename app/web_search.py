"""Constrained web retrieval for the LangGraph web search agent."""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import date
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from app.safety import INJECTION, normalize
from app.observability import web_search_results

log = logging.getLogger(__name__)

_MAX_PAGE_BYTES = 512_000
_MAX_SOURCE_CHARS = 6_000
_QUERY_CLEANUP = re.compile(r"[^\w\s\-+'(),./]", re.UNICODE)


def sanitize_search_query(value: str | None) -> str | None:
    """Accept only a short, plain search query from the model."""
    if not isinstance(value, str):
        return None
    value = value.strip().strip('`"\'')
    value = re.sub(r'^(search query|query)\s*:\s*', '', value, flags=re.IGNORECASE)
    if re.search(r'https?://|\bsite\s*:', value, flags=re.IGNORECASE):
        return None
    value = ' '.join(_QUERY_CLEANUP.sub(' ', value).split())[:200].strip()
    value = ' '.join(value.split()[:16])
    if len(value) < 3 or INJECTION.search(normalize(value)):
        return None
    return value


def _host_allowed(url: str, allowed_domains: set[str]) -> bool:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        return False
    host = parsed.hostname.rstrip('.').lower()
    return any(host == domain or host.endswith('.' + domain) for domain in allowed_domains)


class WebSearchAgent:
    """Search and fetch a small set of allowlisted public medical pages."""

    def __init__(self, settings, search_fn=None, fetch_fn=None):
        self.settings = settings
        self._search_fn = search_fn or self._ddgs_search
        self._fetch_fn = fetch_fn or self._fetch_page

    def allowed_domains(self, category: str) -> set[str]:
        configured = {
            item.strip().lower().lstrip('.')
            for item in self.settings.web_search_trusted_domains.split(',')
            if item.strip()
        }
        if category == 'hospital':
            portal = self.settings.portal_url
            host = urlsplit(portal).hostname if portal else None
            return {host.lower()} if host else set()
        return configured

    def search(self, query: str, category: str) -> list[dict]:
        query = sanitize_search_query(query)
        domains = self.allowed_domains(category)
        if not query or not domains:
            return []

        # Query one allowlisted domain at a time. DuckDuckGo does not reliably
        # support parenthesized OR combinations of multiple site: operators.
        results = []
        for domain in sorted(domains):
            try:
                results.extend(self._search_fn(
                    f'{query} site:{domain}', self.settings.web_search_max_results
                ) or [])
            except Exception as error:
                log.info('Web search domain skipped: domain=%s error=%s', domain, type(error).__name__)
        if not results:
            web_search_results(query, [])
            return []

        sources, seen = [], set()
        try:
            for result in results or []:
                url = str(result.get('href') or result.get('url') or '').strip()
                if not _host_allowed(url, domains) or url in seen:
                    continue
                seen.add(url)
                try:
                    fetched = self._fetch_fn(url, domains)
                except Exception as error:
                    log.info('Web result fetch skipped: %s', type(error).__name__)
                    continue
                if not fetched:
                    continue
                title, text, final_url = fetched
                if INJECTION.search(normalize(text)):
                    log.info('Web result rejected because it contains instruction-like content')
                    continue
                source_id = hashlib.sha256(final_url.encode()).hexdigest()[:20]
                sources.append({
                    'id': f'web:{source_id}',
                    'title': title or str(result.get('title') or 'Trusted medical source')[:180],
                    'url': final_url,
                    'text': text[:_MAX_SOURCE_CHARS],
                    'category': category,
                    'checked_at': date.today().isoformat(),
                    'web': True,
                })
                if len(sources) >= self.settings.web_search_max_results:
                    break
        except Exception as error:
            log.warning('Web search result stream unavailable: %s', type(error).__name__)
        web_search_results(query, sources)
        return sources

    def _ddgs_search(self, query: str, max_results: int):
        # Imported lazily so deployments that keep web search disabled do not
        # require or initialize a network-search client.
        from ddgs import DDGS

        return DDGS(timeout=self.settings.web_search_timeout_seconds).text(
            query,
            backend=self.settings.web_search_backend,
            safesearch='on',
            max_results=max_results,
        )

    def _fetch_page(self, url: str, allowed_domains: set[str]):
        current = url
        timeout = httpx.Timeout(self.settings.web_search_timeout_seconds)
        headers = {'User-Agent': 'MediAgent/1.0 (+public medical information retrieval)'}
        with httpx.Client(timeout=timeout, follow_redirects=False, headers=headers) as client:
            for _ in range(3):
                if not _host_allowed(current, allowed_domains):
                    return None
                with client.stream('GET', current) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get('location')
                        if not location:
                            return None
                        current = urljoin(current, location)
                        continue
                    response.raise_for_status()
                    content_type = response.headers.get('content-type', '').lower()
                    if not (content_type.startswith('text/html') or content_type.startswith('text/plain')):
                        return None
                    body, size = bytearray(), 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > _MAX_PAGE_BYTES:
                            return None
                        body.extend(chunk)
                    final_url = str(response.url)
                    if not _host_allowed(final_url, allowed_domains):
                        return None
                    html = body.decode(response.encoding or 'utf-8', errors='replace')
                    soup = BeautifulSoup(html, 'html.parser')
                    for tag in soup(['script', 'style', 'nav', 'footer', 'form', 'noscript']):
                        tag.decompose()
                    title = soup.title.get_text(' ', strip=True)[:180] if soup.title else ''
                    parts = [item.get_text(' ', strip=True) for item in soup.select('main p, article p, main li, article li')]
                    if not parts:
                        parts = [item.get_text(' ', strip=True) for item in soup.select('p')]
                    text = re.sub(r'\s+', ' ', ' '.join(parts)).strip()
                    return (title, text, final_url) if len(text) >= 80 else None
        return None
