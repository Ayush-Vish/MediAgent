"""Compile the actual Store startup against PostgreSQL without needing a live DB."""
from contextlib import nullcontext
from types import SimpleNamespace

from sqlalchemy import create_mock_engine

import app.store as store_module


def test_postgresql_startup_attaches_and_compiles_approval_index(monkeypatch):
    statements = []
    engine = None

    def execute(statement, *args, **kwargs):
        statements.append(str(statement.compile(dialect=engine.dialect)))
        return SimpleNamespace(first=lambda: None)

    engine = create_mock_engine('postgresql+psycopg://', execute)
    engine.begin = lambda: nullcontext(engine)
    monkeypatch.setattr(store_module, 'create_engine', lambda *args, **kwargs: engine)
    store = store_module.Store('postgresql+psycopg://unused')
    index = next(i for i in store.records.indexes if i.name == 'mediagent_approved_sources')
    assert index.table is store.records
    ddl = next(s for s in statements if 'CREATE INDEX IF NOT EXISTS mediagent_approved_sources' in s)
    assert 'ON mediagent_records' in ddl
    assert "'hospital_id'" in ddl and "'category'" in ddl
    assert "kind = 'source'" in ddl and "'approved'" in ddl
    assert any('ENABLE ROW LEVEL SECURITY' in s for s in statements)
