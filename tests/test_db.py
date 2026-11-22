import sqlite3
from app.db import catalogue_stats, create_quote, import_catalogue, open_db


def test_schema_created(app):
    with open_db(app.state.config["DATABASE_PATH"]) as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"products", "iaquote_enrichments", "iaquote_jobs", "quotes"} <= tables


def test_migration_preserves_existing_product(tmp_path):
    path = tmp_path / "old.sqlite"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE products(id INTEGER PRIMARY KEY, code TEXT, designation TEXT)")
    db.execute("INSERT INTO products VALUES(1,'OLD-1','Produit historique')")
    db.commit(); db.close()
    from app.db import init_schema
    init_schema(str(path))
    with open_db(str(path)) as db:
        row = db.execute("SELECT * FROM products WHERE id=1").fetchone()
        assert row["designation"] == "Produit historique"
        assert "price" in row.keys()


def test_import_catalogue_creates_rows(app):
    csv = "code;designation;prix;stock\nA1;Produit A;12,50;3\nA2;Produit B;9.90;4\n".encode()
    with open_db(app.state.config["DATABASE_PATH"]) as db:
        result = import_catalogue(db, csv)
        assert result == {"created": 2, "updated": 0, "ignored": 0, "total": 2}
        assert db.execute("SELECT COUNT(*) n FROM products").fetchone()["n"] == 2


def test_import_catalogue_is_idempotent(app):
    first = "code,designation,prix\nA1,Produit A,10\n".encode()
    second = "code,designation,prix\nA1,Produit A révisé,15\n".encode()
    with open_db(app.state.config["DATABASE_PATH"]) as db:
        import_catalogue(db, first)
        result = import_catalogue(db, second)
        assert result["updated"] == 1
        row = db.execute("SELECT designation,price FROM products WHERE code='A1'").fetchone()
        assert row["designation"] == "Produit A révisé" and row["price"] == 15


def test_import_ignores_incomplete_rows(app):
    with open_db(app.state.config["DATABASE_PATH"]) as db:
        result = import_catalogue(db, b"code;designation\n;Sans code\nA1;\n")
        assert result["ignored"] == 2


def test_real_stats_do_not_report_false_success(app):
    with open_db(app.state.config["DATABASE_PATH"]) as db:
        import_catalogue(db, b"code;designation\nA;Alpha\nB;Beta\n")
        stats = catalogue_stats(db)
        assert stats == {"total": 2, "verified": 0, "failed": 0, "pending": 0, "missing": 2, "coverage": 0.0}


def test_create_quote_uses_catalogue_price(app):
    with open_db(app.state.config["DATABASE_PATH"]) as db:
        import_catalogue(db, b"code;designation;prix\nA;Alpha;12.5\n")
        pid = db.execute("SELECT id FROM products").fetchone()["id"]
        qid = create_quote(db, "Client", [{"product_id": pid, "quantity": 2}])
        item = db.execute("SELECT * FROM quote_items WHERE quote_id=?", (qid,)).fetchone()
        assert item["quantity"] == 2 and item["unit_price"] == 12.5
