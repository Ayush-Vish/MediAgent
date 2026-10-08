"""Small durable store. A single API worker serializes per-session updates.

SQLite is for local development; PostgreSQL persists cloud state across restarts.
Only sanitized turn snapshots are saved, never LangGraph's raw input state.
"""
import hashlib
import json
import time
from contextlib import contextmanager
from pathlib import Path
from threading import RLock

from sqlalchemy import Column, Float, MetaData, String, Table, Text, create_engine, delete, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.pool import StaticPool


class Store:
    def __init__(self, url: str, retention_hours: int = 24):
        if url.startswith('sqlite:///') and ':memory:' not in url:
            Path(url.removeprefix('sqlite:///')).parent.mkdir(parents=True, exist_ok=True)
        options = {'pool_pre_ping': True}
        if url.startswith('sqlite'):
            options['connect_args'] = {'check_same_thread': False}
            if ':memory:' in url:
                options['poolclass'] = StaticPool
        self.engine = create_engine(url, **options)
        self.ttl = retention_hours * 3600
        self.locks = [RLock() for _ in range(64)]
        self.quota_lock = RLock()
        meta = MetaData()
        self.records = Table('mediagent_records', meta,
                             Column('key', String(160), primary_key=True),
                             Column('kind', String(30), nullable=False, index=True),
                             Column('value', Text, nullable=False),
                             Column('expires', Float, nullable=True, index=True))
        meta.create_all(self.engine)
        if self.engine.dialect.name == 'postgresql':
            with self.engine.begin() as conn:
                conn.execute(text('ALTER TABLE mediagent_records ENABLE ROW LEVEL SECURITY'))
                for role in ('anon', 'authenticated'):
                    exists = conn.execute(text('SELECT 1 FROM pg_roles WHERE rolname = :role'), {'role': role}).first()
                    if exists:
                        conn.execute(text(f'REVOKE ALL ON mediagent_records FROM {role}'))

    @contextmanager
    def lock(self, key):
        index = int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) % len(self.locks)
        with self.locks[index]:
            yield

    def get(self, key):
        with self.engine.connect() as conn:
            row = conn.execute(select(self.records).where(self.records.c.key == key)).mappings().first()
        if row and (row['expires'] is None or row['expires'] > time.time()):
            return json.loads(row['value'])
        return None

    def put(self, key, kind, value, *, permanent=False, expires=None):
        expiry = None if permanent else (expires or time.time() + self.ttl)
        data = dict(key=key, kind=kind, value=json.dumps(value), expires=expiry)
        insert = sqlite_insert if self.engine.dialect.name == 'sqlite' else pg_insert
        statement = insert(self.records).values(**data)
        statement = statement.on_conflict_do_update(index_elements=['key'], set_=data)
        with self.engine.begin() as conn:
            conn.execute(statement)

    def list(self, kind):
        with self.engine.connect() as conn:
            rows = conn.execute(select(self.records).where(self.records.c.kind == kind)).mappings().all()
        now = time.time()
        return [dict(json.loads(row['value']), id=row['key']) for row in rows
                if row['expires'] is None or row['expires'] > now]

    def remove(self, key):
        with self.engine.begin() as conn:
            conn.execute(delete(self.records).where(self.records.c.key == key))

    def prune(self):
        with self.engine.begin() as conn:
            conn.execute(delete(self.records).where(self.records.c.expires < time.time()))

    def consume(self, key, limit, seconds):
        with self.quota_lock:
            now = time.time()
            record = self.get(key) or {'count': 0, 'until': now + seconds}
            if record['count'] >= limit:
                return False
            record['count'] += 1
            self.put(key, 'quota', record, expires=record['until'])
            return True
