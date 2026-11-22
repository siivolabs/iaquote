import io
import os
from unittest.mock import patch

import pytest

from app.app import create_app
from app.enrichment import _process_job, get_bulk_job, parse_product_page, queue_bulk_enrichment

ROOT = os.path.dirname(os.path.dirname(__file__))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db = tmp_path / "test.sqlite"
    monkeypatch.setenv("BOOTSTRAP_CSV", os.path.join(ROOT, "examples", "catalogue-demo.csv"))
    monkeypatch.setenv("PRESEED_PATH", os.path.join(ROOT, "examples", "enrichissement-demo.json"))
    app = create_app({"TESTING": True, "DATABASE_PATH": str(db)})
    return app.test_client()


def test_bootstrap_demo(client):
    data = client.get("/api/health").get_json()
    assert data["ok"] is True
    assert data["catalog_count"] == 9
    assert data["verified_count"] == 9


def test_catalog_contains_demo_reference(client):
    data = client.get("/api/catalog?q=DEMO-CPU-01").get_json()
    assert data["total"] == 1
    assert data["items"][0]["designation"] == "Processeur Démo AM5"
    assert data["items"][0]["sale_price_ttc"] == 249.90


def test_verified_desktop_quote(client):
    with patch("app.quote_engine.ollama.generate_sales_copy", side_effect=ConnectionError("offline test")):
        response = client.post("/api/quotes", json={
            "client_need": "PC fixe pour jouer en 1080p, silencieux et évolutif.",
            "budget_ttc": 1600,
            "strategy": "balanced",
            "machine_type": "desktop",
            "verified_only": True,
        })
    assert response.status_code == 201
    quote = response.get_json()
    assert set(quote["references"].splitlines()) == {
        "DEMO-CPU-01", "DEMO-MB-01", "DEMO-RAM-01", "DEMO-SSD-01",
        "DEMO-GPU-01", "DEMO-PSU-01", "DEMO-CASE-01", "DEMO-COOL-01",
    }
    assert all(check["status"] == "pass" for check in quote["checks"])
    assert quote["ai"]["mode"] == "fallback"


def test_verified_laptop_quote(client):
    with patch("app.quote_engine.ollama.generate_sales_copy", side_effect=ConnectionError("offline test")):
        response = client.post("/api/quotes", json={
            "client_need": "PC portable pour bureautique avec écran lisible.",
            "budget_ttc": 800,
            "machine_type": "auto",
            "verified_only": True,
        })
    quote = response.get_json()
    assert response.status_code == 201
    assert quote["machine_type"] == "laptop"
    assert quote["references"] == "DEMO-LAPTOP-01"
    assert quote["total_ttc"] == 699.90


def test_product_page_parser():
    html = """<html><head><title>Produit Test</title></head><body>
    <h1>Produit Test</h1><table><tr><th>Support du processeur</th><td>AMD AM5</td></tr>
    <tr><th>Type de mémoire</th><td>DDR5</td></tr><tr><th>TDP</th><td>105 W</td></tr></table></body></html>"""
    result = parse_product_page(html, "source-configuree", "Produit Test")
    assert result["status"] == "verified"
    assert result["specs"]["socket"] == "AMD AM5"
    assert result["specs"]["memory_type"] == "DDR5"
    assert result["specs"]["tdp_w"] == 105


def test_reimport_same_csv_is_idempotent(client):
    path = os.path.join(ROOT, "examples", "catalogue-demo.csv")
    raw = open(path, "rb").read()
    response = client.post(
        "/api/import",
        data={"file": (io.BytesIO(raw), "catalogue-demo.csv")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    assert response.get_json()["imported"] == 9
    assert response.get_json()["queued_for_enrichment"] == 0
    assert client.get("/api/health").get_json()["catalog_count"] == 9


def test_new_import_is_automatically_queued(client):
    raw = (
        "Code article;Désignation;Catégorie;Prix TTC €\n"
        "DEMO-NEW-01;Nouveau produit démo;Composant;99,90\n"
    ).encode("utf-8")
    response = client.post(
        "/api/import",
        data={"file": (io.BytesIO(raw), "nouveau.csv")},
        content_type="multipart/form-data",
    )
    data = response.get_json()
    assert response.status_code == 200
    assert data["queued_for_enrichment"] == 1
    # Sans source privée dans les tests, la tâche reste persistée et explique
    # clairement pourquoi elle ne démarre pas au lieu de perdre le produit.
    assert data["enrichment_job"]["status"] == "blocked"


def test_bulk_worker_continues_after_one_product_failure(client, monkeypatch):
    db_path = client.application.config["DATABASE_PATH"]
    monkeypatch.setattr("app.enrichment.technical_source_ready", lambda: True)

    def fake_enrich(code, force=False, db_path=None):
        if code == "DEMO-CPU-01":
            raise RuntimeError("échec simulé")
        return {"enrichment_status": "verified"}

    monkeypatch.setattr("app.enrichment.enrich_product", fake_enrich)
    job = queue_bulk_enrichment(
        ["DEMO-CPU-01", "DEMO-MB-01"], force=True, db_path=db_path, launch=False
    )
    _process_job(job["id"], db_path)
    finished = get_bulk_job(job["id"], db_path)
    assert finished["status"] == "completed_with_errors"
    assert finished["processed"] == 2
    assert finished["verified"] == 1
    assert finished["failed"] == 1
