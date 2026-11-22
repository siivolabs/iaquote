import contextlib
import importlib.util
import sqlite3
import sys
import types
from pathlib import Path
from urllib.parse import quote

import pytest

SOURCE = Path(__file__).parents[1] / "app" / "enrichment.py"


@pytest.fixture
def module(tmp_path):
    package = types.ModuleType("corrective_app")
    package.__path__ = [str(SOURCE.parent)]
    fake_db = types.ModuleType("corrective_app.db")

    @contextlib.contextmanager
    def transaction(db_path=None):
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def utcnow():
        return "2026-11-22T19:00:00+00:00"

    def product_with_enrichment(connection, code):
        row = connection.execute(
            """SELECT p.*, e.source_url, e.source_title,
                      e.status AS enrichment_status, e.confidence,
                      e.specs_json, e.validation_note
               FROM products p
               LEFT JOIN enrichments e ON e.product_code=p.code
               WHERE p.code=?""",
            (code,),
        ).fetchone()
        return dict(row) if row else None

    fake_db.transaction = transaction
    fake_db.utcnow = utcnow
    fake_db.product_with_enrichment = product_with_enrichment
    sys.modules[package.__name__] = package
    sys.modules[fake_db.__name__] = fake_db

    spec = importlib.util.spec_from_file_location(
        "corrective_app.enrichment", SOURCE
    )
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)

    db_path = tmp_path / "catalog.sqlite"
    connection = sqlite3.connect(db_path)
    connection.executescript(
        """
        CREATE TABLE products (
            code TEXT PRIMARY KEY,
            designation TEXT NOT NULL,
            brand TEXT,
            category TEXT
        );
        CREATE TABLE enrichments (
            product_code TEXT PRIMARY KEY,
            source_url TEXT NOT NULL,
            source_title TEXT,
            status TEXT NOT NULL,
            confidence REAL NOT NULL,
            specs_json TEXT NOT NULL,
            raw_summary TEXT,
            validation_note TEXT,
            checked_at TEXT
        );
        CREATE TABLE enrichment_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            status TEXT NOT NULL,
            trigger TEXT NOT NULL,
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
            job_id INTEGER NOT NULL,
            product_code TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            error TEXT,
            processed_at TEXT,
            PRIMARY KEY(job_id, product_code)
        );
        """
    )
    connection.commit()
    connection.close()
    return loaded, db_path


def connect(path):
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def add_products(path, count):
    connection = connect(path)
    connection.executemany(
        "INSERT INTO products(code,designation,brand,category) VALUES(?,?,?,?)",
        [
            (f"P{index:03d}", f"Produit {index}", "Marque", "Catégorie")
            for index in range(1, count + 1)
        ],
    )
    connection.commit()
    connection.close()


def add_job(path, status, total, processed=0, failed=0):
    connection = connect(path)
    cursor = connection.execute(
        """INSERT INTO enrichment_jobs(
               created_at,status,trigger,force,total,processed,failed,last_error
           ) VALUES(?,?,?,?,?,?,?,?)""",
        (
            "2026-11-22T18:00:00+00:00",
            status,
            "test",
            0,
            total,
            processed,
            failed,
            "échec de recherche" if failed else None,
        ),
    )
    connection.commit()
    job_id = cursor.lastrowid
    connection.close()
    return job_id


def test_save_enrichment_accepts_preverified_data_without_source_url(module):
    enrichment, db_path = module
    add_products(db_path, 1)

    enrichment.save_enrichment(
        "P001",
        {
            "status": "verified",
            "confidence": 1.0,
            "specs": {"memory_type": "DDR5"},
        },
        str(db_path),
    )

    connection = connect(db_path)
    saved = connection.execute(
        "SELECT source_url,status,confidence,specs_json FROM enrichments WHERE product_code=?",
        ("P001",),
    ).fetchone()
    connection.close()

    assert saved["source_url"] == ""
    assert saved["status"] == "verified"
    assert saved["confidence"] == 1.0
    assert saved["specs_json"] == '{"memory_type": "DDR5"}'


def test_current_job_reports_catalog_not_latest_empty_job(module):
    enrichment, db_path = module
    add_products(db_path, 291)
    job_id = add_job(db_path, "completed_with_errors", 291, processed=47, failed=47)
    connection = connect(db_path)
    connection.executemany(
        """INSERT INTO enrichment_job_items(job_id,product_code,status,error)
           VALUES(?,?,?,?)""",
        [(job_id, f"P{index:03d}", "failed", "aucune fiche") for index in range(1, 48)],
    )
    for _ in range(5):
        connection.execute(
            """INSERT INTO enrichment_jobs(created_at,finished_at,status,trigger,force,total,processed)
               VALUES(?,?,?,?,?,?,?)""",
            (
                "2026-11-22T18:10:00+00:00",
                "2026-11-22T18:10:00+00:00",
                "completed",
                "manual",
                0,
                0,
                0,
            ),
        )
    connection.commit()
    connection.close()

    current = enrichment.get_bulk_job(None, str(db_path))

    assert current["catalog_summary"] is True
    assert current["total"] == 291
    assert current["processed"] == 0
    assert current["attempted"] == 47
    assert current["failed"] == 47
    assert current["remaining"] == 291
    assert current["progress_percent"] == 0
    assert current["status"] == "completed_with_errors"


def test_queue_reuses_and_unblocks_existing_job(module, monkeypatch):
    enrichment, db_path = module
    add_products(db_path, 2)
    job_id = add_job(db_path, "blocked", 2)
    connection = connect(db_path)
    connection.executemany(
        "INSERT INTO enrichment_job_items(job_id,product_code,status) VALUES(?,?,?)",
        [(job_id, "P001", "queued"), (job_id, "P002", "queued")],
    )
    connection.commit()
    connection.close()
    monkeypatch.setenv("TECHNICAL_ALLOWED_HOSTS", "catalogue.example")

    job = enrichment.queue_bulk_enrichment(db_path=str(db_path), launch=False)

    connection = connect(db_path)
    count = connection.execute("SELECT COUNT(*) FROM enrichment_jobs").fetchone()[0]
    status = connection.execute(
        "SELECT status FROM enrichment_jobs WHERE id=?", (job_id,)
    ).fetchone()[0]
    connection.close()
    assert count == 1
    assert job["id"] == job_id
    assert job["total"] == 2
    assert status == "queued"


def test_queue_does_not_create_empty_job(module, monkeypatch):
    enrichment, db_path = module
    add_products(db_path, 1)
    connection = connect(db_path)
    connection.execute(
        """INSERT INTO enrichments(
               product_code,source_url,source_title,status,confidence,specs_json
           ) VALUES(?,?,?,?,?,?)""",
        (
            "P001",
            "https://catalogue.example/fiche/1",
            "Produit 1",
            "verified",
            0.99,
            "{}",
        ),
    )
    connection.commit()
    connection.close()
    monkeypatch.setenv("TECHNICAL_ALLOWED_HOSTS", "catalogue.example")

    result = enrichment.queue_bulk_enrichment(db_path=str(db_path), launch=False)

    connection = connect(db_path)
    count = connection.execute("SELECT COUNT(*) FROM enrichment_jobs").fetchone()[0]
    connection.close()
    assert count == 0
    assert result["total"] == 1
    assert result["processed"] == 1
    assert result["remaining"] == 0


def test_search_falls_back_to_independent_html_discovery(module, monkeypatch):
    enrichment, _ = module
    monkeypatch.setenv("TECHNICAL_ALLOWED_HOSTS", "catalogue.example")
    monkeypatch.setenv(
        "TECHNICAL_SEARCH_URL",
        "https://catalogue.example/recherche?q={query}",
    )

    class Response:
        def __init__(self, url, text, content_type="text/html"):
            self.url = url
            self.text = text
            self.headers = {"content-type": content_type}

        def json(self):
            raise ValueError("not json")

    product_url = "https://catalogue.example/fiche/produit-1"
    redirect = "https://duckduckgo.com/l/?uddg=" + quote(product_url, safe="")
    responses = iter(
        [
            Response(
                "https://catalogue.example/recherche?q=Produit+1",
                "<html><body><p>Une erreur est survenue</p></body></html>",
            ),
            Response(
                "https://html.duckduckgo.com/html/?q=Produit+1",
                f'<html><body><a href="{redirect}">Produit 1 Marque</a></body></html>',
            ),
        ]
    )
    monkeypatch.setattr(enrichment, "_get_search", lambda url: next(responses))

    candidates = enrichment.search_candidates("Produit 1", "Marque")

    assert len(candidates) == 1
    assert candidates[0]["url"] == product_url
    assert candidates[0]["score"] > 0.7


def test_json_discovery_is_supported(module, monkeypatch):
    enrichment, _ = module
    monkeypatch.setenv("TECHNICAL_ALLOWED_HOSTS", "catalogue.example")
    monkeypatch.setenv(
        "TECHNICAL_SEARCH_API_URL",
        "https://search.example/search?q={query}&format=json",
    )
    monkeypatch.setenv(
        "TECHNICAL_SEARCH_ALLOWED_HOSTS",
        "search.example",
    )
    monkeypatch.setenv("TECHNICAL_DEFAULT_DISCOVERY", "false")

    class Response:
        url = "https://search.example/search?q=Produit+1&format=json"
        text = '{"results": []}'
        headers = {"content-type": "application/json"}

        @staticmethod
        def json():
            return {
                "results": [
                    {
                        "url": "https://catalogue.example/fiche/produit-1",
                        "title": "Produit 1 Marque",
                    }
                ]
            }

    monkeypatch.setattr(enrichment, "_get_search", lambda url: Response())

    candidates = enrichment.search_candidates("Produit 1", "Marque")

    assert candidates[0]["url"] == "https://catalogue.example/fiche/produit-1"


def test_resume_requeues_interrupted_item(module, monkeypatch):
    enrichment, db_path = module
    add_products(db_path, 1)
    job_id = add_job(db_path, "running", 1)
    connection = connect(db_path)
    connection.execute(
        "INSERT INTO enrichment_job_items(job_id,product_code,status) VALUES(?,?,?)",
        (job_id, "P001", "running"),
    )
    connection.commit()
    connection.close()
    monkeypatch.setenv("TECHNICAL_ALLOWED_HOSTS", "catalogue.example")
    monkeypatch.setattr(enrichment, "_ensure_worker", lambda db_path=None: True)

    queued = enrichment.resume_bulk_enrichment(str(db_path))

    connection = connect(db_path)
    job_status = connection.execute(
        "SELECT status FROM enrichment_jobs WHERE id=?", (job_id,)
    ).fetchone()[0]
    item_status = connection.execute(
        "SELECT status FROM enrichment_job_items WHERE job_id=?", (job_id,)
    ).fetchone()[0]
    connection.close()
    assert queued == 1
    assert job_status == "queued"
    assert item_status == "queued"
