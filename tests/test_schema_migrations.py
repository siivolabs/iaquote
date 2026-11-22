"""End-to-end regressions for real V0.3.0/V0.3.1 SQLite layouts."""
from __future__ import annotations

import json
import sqlite3

from app import create_app
from app.catalog import import_products
from app.db import SCHEMA_VERSION, init_db, transaction
from app.enrichment import get_bulk_job, run_bulk_job


def _create_real_legacy_database(path: str) -> dict[str, int]:
    con = sqlite3.connect(path)
    con.executescript(
        """
        PRAGMA foreign_keys=ON;
        CREATE TABLE products (
            code TEXT PRIMARY KEY,
            family_code TEXT,
            designation TEXT NOT NULL,
            category TEXT,
            brand TEXT,
            reference_type TEXT,
            purchase_price_ht REAL NOT NULL DEFAULT 0,
            margin_ht REAL NOT NULL DEFAULT 0,
            margin_rate REAL NOT NULL DEFAULT 0,
            crossed_price REAL NOT NULL DEFAULT 0,
            sale_price_ttc REAL NOT NULL DEFAULT 0,
            stock_central INTEGER NOT NULL DEFAULT 0,
            stock_shop INTEGER NOT NULL DEFAULT 0,
            orders_pending INTEGER NOT NULL DEFAULT 0,
            obsolete INTEGER NOT NULL DEFAULT 0,
            sales_7d INTEGER NOT NULL DEFAULT 0,
            sales_14d INTEGER NOT NULL DEFAULT 0,
            sales_28d INTEGER NOT NULL DEFAULT 0,
            representative TEXT,
            source_updated_at TEXT,
            imported_at TEXT NOT NULL
        );
        CREATE TABLE enrichments (
            product_code TEXT PRIMARY KEY REFERENCES products(code) ON DELETE CASCADE,
            source_url TEXT,
            source_title TEXT,
            status TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 0,
            specs_json TEXT NOT NULL DEFAULT '{}',
            raw_summary TEXT,
            validation_note TEXT,
            fetched_at TEXT NOT NULL
        );
        CREATE TABLE enrichment_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            status TEXT NOT NULL,
            trigger TEXT NOT NULL DEFAULT 'manual',
            force INTEGER NOT NULL DEFAULT 0,
            total INTEGER NOT NULL DEFAULT 0,
            processed INTEGER NOT NULL DEFAULT 0,
            verified INTEGER NOT NULL DEFAULT 0,
            review INTEGER NOT NULL DEFAULT 0,
            failed INTEGER NOT NULL DEFAULT 0,
            cancelled INTEGER NOT NULL DEFAULT 0,
            last_error TEXT
        );
        CREATE TABLE enrichment_job_items (
            job_id INTEGER NOT NULL REFERENCES enrichment_jobs(id) ON DELETE CASCADE,
            product_code TEXT NOT NULL REFERENCES products(code) ON DELETE CASCADE,
            status TEXT NOT NULL DEFAULT 'queued',
            error TEXT,
            processed_at TEXT,
            PRIMARY KEY(job_id, product_code)
        );
        CREATE TABLE quotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            client_need TEXT NOT NULL,
            budget_ttc REAL NOT NULL,
            strategy TEXT NOT NULL,
            quote_json TEXT NOT NULL
        );
        """
    )
    timestamp = "2026-11-22T20:00:00+00:00"
    products = []
    for number in range(1, 292):
        code = "S0111966" if number == 1 else f"EXISTING-{number:03d}"
        designation = "Intel Core Ultra 5 225" if number == 1 else f"Produit {number}"
        products.append((code, designation, "Composants", 100 + number, number % 5, timestamp))
    con.executemany(
        """INSERT INTO products(
               code,designation,category,sale_price_ttc,stock_shop,imported_at
           ) VALUES(?,?,?,?,?,?)""",
        products,
    )
    con.execute(
        """INSERT INTO enrichments(
               product_code,source_url,source_title,status,confidence,specs_json,
               raw_summary,validation_note,fetched_at
           ) VALUES(?,?,?,?,?,?,?,?,?)""",
        (
            "EXISTING-002",
            "https://catalog.example/fiche/2",
            "Produit 2",
            "verified",
            1.0,
            '{"legacy": true}',
            "ancienne fiche",
            "validée",
            timestamp,
        ),
    )
    cursor = con.execute(
        """INSERT INTO enrichment_jobs(
               created_at,started_at,finished_at,status,trigger,force,total,processed,
               verified,review,failed,cancelled,last_error
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (timestamp, timestamp, timestamp, "cancelled", "manual", 0, 1, 1, 0, 0, 1, 0, "ancienne erreur"),
    )
    job_id = int(cursor.lastrowid)
    con.execute(
        """INSERT INTO enrichment_job_items(job_id,product_code,status,error,processed_at)
           VALUES(?,?,?,?,?)""",
        (job_id, "EXISTING-003", "failed", "ancienne erreur article", timestamp),
    )
    con.execute(
        """INSERT INTO quotes(created_at,client_need,budget_ttc,strategy,quote_json)
           VALUES(?,?,?,?,?)""",
        (timestamp, "Besoin historique", 321.0, "desktop", json.dumps({"items": [{"code": "EXISTING-002"}]})),
    )
    con.commit()
    con.close()
    return {"job_id": job_id, "products": 291, "jobs": 1, "items": 1, "quotes": 1, "enrichments": 1}


def _legacy_app(path: str):
    return create_app(
        {
            "TESTING": True,
            "DATABASE_PATH": path,
            "START_BACKGROUND_WORKER": False,
        }
    )


def test_automatic_migration_preserves_291_products_jobs_items_quotes_and_enrichments(tmp_path, monkeypatch):
    path = str(tmp_path / "legacy.sqlite")
    before = _create_real_legacy_database(path)
    monkeypatch.delenv("BOOTSTRAP_CSV", raising=False)
    monkeypatch.delenv("PRESEED_PATH", raising=False)

    app = _legacy_app(path)
    assert app.test_client().get("/api/health").status_code == 200

    with transaction(path) as con:
        assert con.execute("SELECT COUNT(*) FROM products").fetchone()[0] == before["products"]
        assert con.execute("SELECT COUNT(*) FROM enrichment_jobs").fetchone()[0] == before["jobs"]
        assert con.execute("SELECT COUNT(*) FROM enrichment_job_items").fetchone()[0] == before["items"]
        assert con.execute("SELECT COUNT(*) FROM quotes").fetchone()[0] == before["quotes"]
        assert con.execute("SELECT COUNT(*) FROM enrichments").fetchone()[0] == before["enrichments"]
        job = con.execute("SELECT * FROM enrichment_jobs WHERE id=?", (before["job_id"],)).fetchone()
        assert job["last_error"] == "ancienne erreur"
        assert job["updated_at"] == job["finished_at"]
        item = con.execute(
            "SELECT * FROM enrichment_job_items WHERE job_id=?", (before["job_id"],)
        ).fetchone()
        assert item["error"] == "ancienne erreur article"
        assert item["finished_at"] == item["processed_at"]
        assert item["attempts"] == 1
        enrichment = con.execute(
            "SELECT * FROM enrichments WHERE product_code='EXISTING-002'"
        ).fetchone()
        assert json.loads(enrichment["specs_json"]) == {"legacy": True}
        quote = con.execute("SELECT * FROM quotes WHERE id=1").fetchone()
        assert quote["client_need"] == "Besoin historique"
        assert quote["total"] == 321.0
        assert json.loads(quote["items_json"])["items"][0]["code"] == "EXISTING-002"
        versions = [row[0] for row in con.execute(
            "SELECT version FROM iaquote_schema_migrations ORDER BY version"
        )]
        assert versions == list(range(1, SCHEMA_VERSION + 1))
        assert con.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_post_jobs_and_article_code_enrichment_work_after_real_legacy_migration(tmp_path, monkeypatch):
    path = str(tmp_path / "legacy-enrichment.sqlite")
    before = _create_real_legacy_database(path)
    monkeypatch.delenv("BOOTSTRAP_CSV", raising=False)
    monkeypatch.delenv("PRESEED_PATH", raising=False)
    app = _legacy_app(path)
    client = app.test_client()

    response = client.post(
        "/api/enrich/jobs",
        json={"codes": ["S0111966"], "retry_failed": True},
    )
    assert response.status_code == 201
    payload = response.get_json()
    assert payload["ok"] is True and payload["created"] is True
    job_id = payload["job"]["id"]
    assert payload["job"]["total"] == 1

    calls = []

    def fetcher(code: str, designation: str):
        calls.append((code, designation))
        return {
            "status": "verified",
            "source_url": "https://catalog.example/fiche/intel-core-ultra-5-225",
            "source_title": "Intel Core Ultra 5 225 - Processeur",
            "confidence": 1.0,
            "specs": {"Socket": "LGA1851", "Code article": code},
        }

    completed = run_bulk_job(job_id, path, fetcher=fetcher, delay=0)
    assert calls == [("S0111966", "Intel Core Ultra 5 225")]
    assert completed["status"] == "finished"
    assert completed["verified"] == 1 and completed["processed"] == 1
    assert get_bulk_job(job_id, path)["items"][0]["product_code"] == "S0111966"
    with transaction(path) as con:
        saved = con.execute(
            "SELECT status,source_url,specs_json FROM enrichments WHERE product_code='S0111966'"
        ).fetchone()
        assert saved["status"] == "verified"
        assert json.loads(saved["specs_json"])["Code article"] == "S0111966"
        assert con.execute("SELECT COUNT(*) FROM products").fetchone()[0] == before["products"]


def test_migration_is_idempotent_and_legacy_tables_accept_new_data(tmp_path, monkeypatch):
    path = str(tmp_path / "legacy-idempotent.sqlite")
    before = _create_real_legacy_database(path)
    monkeypatch.delenv("BOOTSTRAP_CSV", raising=False)
    monkeypatch.delenv("PRESEED_PATH", raising=False)
    app = _legacy_app(path)

    init_db(path)
    init_db(path)
    imported = import_products(
        [{"code": "NEW-LEGACY", "designation": "Nouveau produit", "category": "Test", "price": 42, "stock": 3}],
        path,
    )
    assert imported["created"] == 1

    client = app.test_client()
    client.post(
        "/api/enrich/manual",
        json={"code": "NEW-LEGACY", "status": "verified", "specs": {"ok": True}},
    )
    quote_response = client.post(
        "/api/quotes",
        json={"customer_name": "Client", "kind": "desktop", "codes": ["NEW-LEGACY"]},
    )
    assert quote_response.status_code == 201

    with transaction(path) as con:
        assert con.execute("SELECT COUNT(*) FROM products").fetchone()[0] == before["products"] + 1
        assert con.execute("SELECT COUNT(*) FROM enrichment_jobs").fetchone()[0] == before["jobs"]
        assert con.execute("SELECT COUNT(*) FROM enrichment_job_items").fetchone()[0] == before["items"]
        assert con.execute("SELECT COUNT(*) FROM iaquote_schema_migrations").fetchone()[0] == SCHEMA_VERSION
        product = con.execute("SELECT * FROM products WHERE code='NEW-LEGACY'").fetchone()
        assert product["imported_at"] and product["price"] == product["sale_price_ttc"] == 42
        new_quote = con.execute("SELECT * FROM quotes ORDER BY id DESC LIMIT 1").fetchone()
        assert new_quote["client_need"] == "Client"
        assert new_quote["quote_json"] and new_quote["items_json"]
