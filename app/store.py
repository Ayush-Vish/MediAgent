"""Small durable store. A single API worker serializes per-session updates.

SQLite is for local development; PostgreSQL persists cloud state across restarts.
Anonymous turn snapshots are sanitized; authenticated patient messages are durable.
LangGraph's raw input state is never saved.
"""
import hashlib
import json
import time
from contextlib import contextmanager
from pathlib import Path
from threading import RLock

from sqlalchemy import (
    JSON, Column, Float, Index, MetaData, String, Table, Text, cast, create_engine,
    delete, func, literal_column, select, text,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CreateIndex


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

        def attribute(name):
            if self.engine.dialect.name == 'sqlite':
                return func.json_extract(self.records.c.value, literal_column("'$." + name + "'"))
            return cast(self.records.c.value, JSON)[literal_column("'" + name + "'")].as_string()

        self.source_hospital = attribute('hospital_id')
        self.source_category = attribute('category')
        self.source_approved = attribute('approved') == (1 if self.engine.dialect.name == 'sqlite' else 'true')
        predicate = (self.records.c.kind == 'source') & self.source_approved
        meta.create_all(self.engine)
        index = Index('mediagent_approved_sources', self.source_hospital, self.source_category,
                      sqlite_where=predicate, postgresql_where=predicate)
        # PostgreSQL JSON expressions do not infer their owning table for Index.
        self.records.append_constraint(index)
        with self.engine.begin() as conn:
            conn.execute(CreateIndex(index, if_not_exists=True))
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

    def get_many(self, keys):
        if not keys:
            return {}
        with self.engine.connect() as conn:
            rows = conn.execute(select(self.records).where(self.records.c.key.in_(keys))).mappings().all()
        now = time.time()
        return {row['key']: json.loads(row['value']) for row in rows
                if row['expires'] is None or row['expires'] > now}

    def put_many(self, records):
        """Atomically upsert a bounded batch of permanent offline ingestion records."""
        if not records:
            return
        data = [dict(key=key, kind=kind, value=json.dumps(value), expires=None)
                for key, kind, value in records]
        insert = sqlite_insert if self.engine.dialect.name == 'sqlite' else pg_insert
        statement = insert(self.records)
        statement = statement.on_conflict_do_update(
            index_elements=['key'], set_={name: getattr(statement.excluded, name)
                                         for name in ('kind', 'value', 'expires')})
        with self.engine.begin() as conn:
            conn.execute(statement, data)

    def list(self, kind):
        with self.engine.connect() as conn:
            rows = conn.execute(select(self.records).where(self.records.c.kind == kind)).mappings().all()
        now = time.time()
        return [dict(json.loads(row['value']), id=row['key']) for row in rows
                if row['expires'] is None or row['expires'] > now]

    def approved_sources(self, hospital_id, category):
        """Filter indexed approval metadata before reading large pending corpora."""
        statement = select(self.records.c.value).where(
            self.records.c.kind == 'source', self.source_approved,
            self.source_hospital == hospital_id, self.source_category == category,
            (self.records.c.expires.is_(None) | (self.records.c.expires > time.time())))
        with self.engine.connect() as conn:
            values = conn.execute(statement).scalars().all()
        return [json.loads(value) for value in values]

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
