import importlib
from app import create_app


def test_package_exports_factory():
    assert callable(create_app)
    assert callable(importlib.import_module("app").create_app)


def test_factory_creates_testing_app(tmp_path):
    app = create_app({"TESTING": True, "DATABASE_PATH": str(tmp_path / "a.sqlite")})
    assert app.testing


def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok", "version": "0.3.2", "database": "ok"}


def test_pages_return_200(client):
    for path in ["/", "/dashboard", "/catalog", "/enrichment", "/history", "/quote"]:
        assert client.get(path).status_code == 200


def test_missing_page(client):
    assert client.get("/not-there").status_code == 404
    assert client.get("/api/not-there").get_json()["ok"] is False
