import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, environment='local', database_url=f'sqlite:///{tmp_path / "test.db"}',
                    admin_token='test-staff-token', source_max_age_days=10000)


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    with TestClient(app) as client:
        client.post('/api/session')
        yield client


@pytest.fixture
def staff():
    return {'Authorization': 'Bearer test-staff-token'}
