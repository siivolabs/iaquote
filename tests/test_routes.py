import io
from app.db import import_catalogue, open_db


def test_health_reports_version(client):
    data = client.get("/api/health").json()
    assert data == {"status": "ok", "version": "0.3.2", "database": "ok"}


def test_dashboard_is_dark_app(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Tableau de bord" in response.content


def test_catalogue_import_route(client):
    response = client.post("/catalogue", files={"catalogue": ("catalogue.csv", b"code;designation\nA1;Produit A\n", "text/csv")}, data={"enqueue":"no"}, follow_redirects=True)
    assert response.status_code == 200
    assert b"Produit A" in response.content


def test_catalogue_search(client, app):
    with open_db(app.state.config["DATABASE_PATH"]) as db:
        import_catalogue(db, b"code;designation\nABC;Cible\nXYZ;Autre\n")
    page = client.get("/catalogue?q=ABC")
    assert b"Cible" in page.content and b"Autre" not in page.content


def test_empty_job_is_honest(client):
    response = client.post("/enrichissement/lancer", data={"mode": "missing_or_failed"}, follow_redirects=True)
    assert response.status_code == 200
    assert b"0 / 0" in response.content


def test_stats_api(client, app):
    with open_db(app.state.config["DATABASE_PATH"]) as db:
        import_catalogue(db, b"code;designation\nA;Alpha\n")
    data = client.get("/api/stats").json()
    assert data["catalogue"]["total"] == 1
    assert data["catalogue"]["missing"] == 1


def test_create_quote_route(client, app):
    with open_db(app.state.config["DATABASE_PATH"]) as db:
        import_catalogue(db, b"code;designation;prix\nA;Alpha;10\n")
        pid = db.execute("SELECT id FROM products").fetchone()["id"]
    response = client.post("/devis/nouveau", data={"customer_name": "Test", "product_id": [str(pid)], "quantity": ["3"]}, follow_redirects=True)
    assert response.status_code == 200 and b"30.00" in response.content
