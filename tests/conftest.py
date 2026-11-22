import pytest
from fastapi.testclient import TestClient
from app import create_app


@pytest.fixture
def app(tmp_path):
    return create_app({
        "TESTING": True,
        "SECRET_KEY": "test-secret-key",
        "DATABASE_PATH": str(tmp_path / "test.sqlite"),
        "CATALOG_SEARCH_BASE_URL": "http://catalog.test/recherche/{code}",
        "CATALOG_REQUEST_DELAY": 0,
        "CATALOG_MATCH_THRESHOLD": 0.50,
    })


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client
