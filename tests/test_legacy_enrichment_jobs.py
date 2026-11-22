"""Regression tests for databases created by IAQuote V0.3.1.

V0.3.1 used a composite key for ``enrichment_job_items`` and did not have
an ``id`` column. These tests deliberately recreate that schema before the
application starts.
"""
from __future__ import annotations

import pytest

from app import create_app
from app.db import init_db, transaction, utcnow
from app.enrichment import get_bulk_job, run_bulk_job


def _create_v031_database(path: str, *, item_status: str = "failed") -> int:
    init_db(path)
    now = utcnow()
    with transaction(path) as con:
        # Recreate only the table whose V0.3.1 shape differs from V0.3.2.
        con.execute("DROP TABLE enrichment_job_items")
        con.execute(
            """
            CREATE TABLE enrichment_job_items (
                job_id INTEGER NOT NULL,
                product_code TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                error TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                started_at TEXT,
                finished_at TEXT,
                PRIMARY KEY (job_id, product_code),
                FOREIGN KEY(job_id) REFERENCES enrichment_jobs(id) ON DELETE CASCADE,
                FOREIGN KEY(product_code) REFERENCES products(code)
                    ON UPDATE CASCADE ON DELETE CASCADE
            )
            """
        )
        con.execute(
            """INSERT INTO products(code,designation,created_at,updated_at)
               VALUES('LEGACY-001','Produit hérité',?,?)""",
            (now, now),
        )
        processed = 0 if item_status == "pending" else 1
        failed = 1 if item_status == "failed" else 0
        status = "pending" if item_status == "pending" else "finished"
        cursor = con.execute(
            """INSERT INTO enrichment_jobs(
                   status,total,processed,verified,review,failed,created_at,updated_at,finished_at
               ) VALUES(?,1,?,0,0,?,?,?,?)""",
            (status, processed, failed, now, now, now if status == "finished" else None),
        )
        job_id = int(cursor.lastrowid)
        con.execute(
            """INSERT INTO enrichment_job_items(
                   job_id,product_code,status,error,attempts,started_at,finished_at
               ) VALUES(?,?,?,?,1,?,?)""",
            (
                job_id,
                "LEGACY-001",
                item_status,
                "ancienne erreur" if item_status == "failed" else None,
                now if item_status != "pending" else None,
                now if item_status == "failed" else None,
            ),
        )
    return job_id


@pytest.fixture()
def legacy_app(tmp_path, monkeypatch):
    database = tmp_path / "v0.3.1.sqlite"
    _create_v031_database(str(database))
    monkeypatch.delenv("BOOTSTRAP_CSV", raising=False)
    monkeypatch.delenv("PRESEED_PATH", raising=False)
    return create_app(
        {
            "TESTING": True,
            "DATABASE_PATH": str(database),
            "START_BACKGROUND_WORKER": False,
        }
    )


def test_enrichment_page_supports_v031_job_items_without_id(legacy_app):
    response = legacy_app.test_client().get("/enrichment")

    assert response.status_code == 200
    assert "LEGACY-001" in response.get_data(as_text=True)
    assert "Produit hérité" in response.get_data(as_text=True)


def test_current_job_api_supports_v031_job_items_without_id(legacy_app):
    response = legacy_app.test_client().get("/api/enrich/jobs/current")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["job"]["status"] == "finished"
    assert payload["job"]["items"][0]["product_code"] == "LEGACY-001"
    assert payload["job"]["items"][0]["error"] == "ancienne erreur"
    assert payload["catalog"]["total"] == 1


def test_worker_processes_v031_job_items_without_id(tmp_path):
    database = str(tmp_path / "v0.3.1-worker.sqlite")
    job_id = _create_v031_database(database, item_status="pending")

    def fetcher(code: str, designation: str):
        return {
            "status": "verified",
            "source_url": "https://catalogue.example/fiche/legacy",
            "source_title": designation,
            "confidence": 1.0,
            "specs": {"code": code},
        }

    completed = run_bulk_job(job_id, database, fetcher=fetcher, delay=0)

    assert completed["status"] == "finished"
    assert completed["verified"] == 1
    assert completed["items"][0]["status"] == "verified"
    assert get_bulk_job(job_id, database)["processed"] == 1


def test_startup_preserves_291_existing_products_and_legacy_data(tmp_path, monkeypatch):
    database = str(tmp_path / "v0.3.1-catalog.sqlite")
    job_id = _create_v031_database(database)
    now = utcnow()
    with transaction(database) as con:
        con.executemany(
            """INSERT INTO products(code,designation,created_at,updated_at)
               VALUES(?,?,?,?)""",
            [(f"EXISTING-{number:03d}", f"Produit {number}", now, now) for number in range(2, 292)],
        )

    monkeypatch.delenv("BOOTSTRAP_CSV", raising=False)
    monkeypatch.delenv("PRESEED_PATH", raising=False)
    application = create_app(
        {
            "TESTING": True,
            "DATABASE_PATH": database,
            "START_BACKGROUND_WORKER": False,
        }
    )

    with transaction(database) as con:
        assert con.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 291
        item = con.execute(
            """SELECT status,error FROM enrichment_job_items
               WHERE job_id=? AND product_code='LEGACY-001'""",
            (job_id,),
        ).fetchone()
    assert dict(item) == {"status": "failed", "error": "ancienne erreur"}
    assert application.test_client().get("/api/health").status_code == 200
