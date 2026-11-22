import io
from app.catalog import import_csv, list_products, parse_csv


def test_parse_semicolon_french_headers():
    rows = parse_csv("Référence;Désignation;Prix;Stock\nABC;Produit test;12,50;3\n")
    assert rows == [{"code": "ABC", "designation": "Produit test", "category": "", "price": 12.5, "stock": 3}]


def test_parse_comma_headers():
    rows = parse_csv("sku,name,price,qty\nX1,Widget,8.25,2\n")
    assert rows[0]["code"] == "X1" and rows[0]["stock"] == 2


def test_parse_requires_code_and_designation():
    try:
        parse_csv("foo;bar\nx;y\n")
    except ValueError as exc:
        assert "code" in str(exc)
    else:
        raise AssertionError("ValueError expected")


def test_bootstrap_demo(app):
    products = list_products(app.config["DATABASE_PATH"])
    assert len(products) == 4


def test_catalog_contains_demo_reference(client):
    payload = client.get("/api/catalog").get_json()
    assert any(item["code"] == "DEMO-CPU-01" for item in payload["items"])


def test_reimport_same_csv_is_idempotent(app):
    content = open("examples/catalogue-demo.csv", "rb").read()
    first = import_csv(content, app.config["DATABASE_PATH"])
    second = import_csv(content, app.config["DATABASE_PATH"])
    assert first["created"] == second["created"] == 0
    assert len(list_products(app.config["DATABASE_PATH"])) == 4


def test_new_import_is_automatically_queued(client):
    data = {"file": (io.BytesIO(b"code;designation\nNEW-1;New product\n"), "catalog.csv")}
    response = client.post("/api/catalog/import", data=data, content_type="multipart/form-data")
    assert response.status_code == 201
    body = response.get_json()
    assert body["created"] == 1 and body["job_id"]


def test_catalog_search(client):
    body = client.get("/api/catalog?q=CPU").get_json()
    assert [item["code"] for item in body["items"]] == ["DEMO-CPU-01"]
