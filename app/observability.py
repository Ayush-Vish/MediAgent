"""Structured pipeline events. Never serialize method arguments or exceptions."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
import json
import logging
import sys
import unicodedata

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text
    HAS_RICH = True
except ImportError:
    HAS_RICH = False
    Console = Panel = Text = None

from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import RLock
from time import perf_counter
from uuid import uuid4

request_id = ContextVar('request_id', default=None)
logger = logging.getLogger('mediagent.pipeline')
_lock = RLock()
_show_chunks = False
_show_query = False


def terminal_text(value):
    return ''.join(c for c in str(value) if c in '\n\t' or not unicodedata.category(c).startswith('C'))


class PrettyHandler(logging.Handler):
    def __init__(self, console=None):
        super().__init__()
        self.console = (console or Console(stderr=True)) if HAS_RICH else None

    def emit(self, record):
        data = getattr(record, 'event_data', {})
        if not data:
            return
        name = data.get('event', '')
        if not HAS_RICH or self.console is None:
            time_str = datetime.fromtimestamp(record.created).strftime('%H:%M:%S')
            req = (data.get('request_id') or 'startup')[:8]
            details = '  '.join(f'{key}={terminal_text(value)}' for key, value in data.items()
                                if key not in {'event', 'request_id'} and value is not None)
            sys.stderr.write(f"{time_str}  [{req}]  {name}  {details}\n")
            if hasattr(record, 'query'):
                sys.stderr.write('User query: ' + terminal_text(record.query) + '\n')
            return
        color = ('red' if any(x in name for x in ('failed', 'exhausted', 'disabled', 'unavailable'))
                 else 'yellow' if any(x in name for x in ('fallback', 'cooldown', 'skipped', 'no_result'))
                 else 'green' if name.endswith(('completed', 'selected')) else 'cyan')
        line = Text(datetime.fromtimestamp(record.created).strftime('%H:%M:%S'), style='dim')
        line.append(f"  [{(data.get('request_id') or 'startup')[:8]}]  ", style='magenta')
        line.append(name, style=f'bold {color}')
        details = '  '.join(f'{key}={terminal_text(value)}' for key, value in data.items()
                            if key not in {'event', 'request_id'} and value is not None)
        if details:
            line.append('  ' + details, style='dim')
        renderables = [line]
        if hasattr(record, 'query'):
            renderables.append(Panel(Text(terminal_text(record.query)), title='User query', border_style='magenta'))
        for index, source in enumerate(getattr(record, 'chunks', []), 1):
            body = Text(terminal_text(source.get('title', 'Untitled')), style='bold cyan')
            body.append('\n' + terminal_text(source.get('url', '')), style='dim')
            body.append('\n\n' + terminal_text(source.get('text', '')))
            renderables.append(Panel(body, title=f'Chunk {index}', border_style='blue'))
        self.console.print(*renderables, sep='\n')


class JsonFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({'timestamp': datetime.now(timezone.utc).isoformat(),
                           'level': record.levelname, **record.event_data}, ensure_ascii=True)


def configure_logging(path='runtime/logs/mediagent.jsonl', show_chunks=False, show_query=False):
    global _show_chunks, _show_query
    with _lock:
        _show_chunks = show_chunks
        _show_query = show_query
        target = str(Path(path).resolve()) if path else ''
        if getattr(logger, '_target', None) == target:
            return
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            handler.close()
        handlers = [PrettyHandler()]
        if target:
            Path(target).parent.mkdir(parents=True, exist_ok=True)
            handlers.append(RotatingFileHandler(target, maxBytes=5_000_000, backupCount=3, encoding='utf-8'))
        for handler in handlers:
            handler.setFormatter(JsonFormatter())
            logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger._target = target


def event(name, **fields):
    logger.info(name, extra={'event_data': {'event': name, 'request_id': request_id.get(), **fields}})


def retrieved_chunks(sources):
    if _show_chunks:
        logger.info('retrieval.chunks', extra={
            'event_data': {'event': 'retrieval.chunks', 'request_id': request_id.get(), 'count': len(sources)},
            'chunks': [{'title': s['title'], 'url': s['url'], 'text': s['text']} for s in sources],
        })


def user_query(message):
    if _show_query:
        logger.info('chat.query', extra={
            'event_data': {'event': 'chat.query', 'request_id': request_id.get()},
            'query': message,
        })


@contextmanager
def span(name, **fields):
    token = request_id.set(str(uuid4())) if request_id.get() is None else None
    started = perf_counter()
    event(name + '.started', **fields)
    try:
        yield
    except Exception as error:
        event(name + '.failed', duration_ms=round((perf_counter() - started) * 1000, 2),
              error_type=type(error).__name__, **fields)
        raise
    else:
        event(name + '.completed', duration_ms=round((perf_counter() - started) * 1000, 2), **fields)
    finally:
        if token is not None:
            request_id.reset(token)


def traced(name):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            with span(name):
                result = function(*args, **kwargs)
                if isinstance(result, list):
                    event(name + '.result', count=len(result))
                elif isinstance(result, dict):
                    answer = result.get('answer')
                    if not isinstance(answer, dict):
                        answer = result
                    event(name + '.result', route=answer.get('route'), mode=answer.get('mode'),
                          category=answer.get('category'), guarded=bool(answer.get('guard_decision')),
                          evidence=answer.get('evidence'), source_count=len(answer.get('sources', [])))
                elif name == 'retrieval' and isinstance(result, tuple):
                    event(name + '.result', source_count=len(result[0]), mode=result[1])
                elif result is None:
                    event(name + '.no_result')
                return result
        return wrapped
    return decorate
