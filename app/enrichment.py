from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from .catalog_client import CatalogueClient
from .db import open_db, record_event, utcnow

_LOCK = threading.Lock()
_RUNNING: set[int] = set()


def create_job(path: str, mode: str = "missing_or_failed", product_ids: list[int] | None = None) -> int:
    if mode not in {"missing", "failed", "missing_or_failed", "all"}:
        raise ValueError("Mode d'enrichissement invalide")
    with open_db(path) as db:
        if product_ids:
            placeholders = ",".join("?" for _ in product_ids)
            rows = db.execute(f"SELECT id FROM products WHERE id IN ({placeholders}) ORDER BY id", product_ids).fetchall()
        else:
            filters = {
                "missing": "e.product_id IS NULL OR e.status='missing'",
                "failed": "e.status='failed'",
                "missing_or_failed": "e.product_id IS NULL OR e.status IN ('missing','failed')",
                "all": "1=1",
            }
            rows = db.execute(
                f"SELECT p.id FROM products p LEFT JOIN iaquote_enrichments e ON e.product_id=p.id WHERE {filters[mode]} ORDER BY p.id"
            ).fetchall()
        now = utcnow()
        cur = db.execute(
            "INSERT INTO iaquote_jobs(status, mode, total, created_at, updated_at) VALUES('queued',?,?,?,?)",
            (mode, len(rows), now, now),
        )
        job_id = cur.lastrowid
        db.executemany(
            "INSERT INTO iaquote_job_items(job_id, product_id, status) VALUES(?,?,'pending')",
            ((job_id, row["id"]) for row in rows),
        )
        if not rows:
            db.execute("UPDATE iaquote_jobs SET status='completed', finished_at=?, updated_at=? WHERE id=?", (now, now, job_id))
        record_event(db, "enrichment.queued", f"Traitement #{job_id} créé", {"mode": mode, "total": len(rows)})
        db.commit()
        return job_id


def _upsert_pending(db, product_id: int):
    now = utcnow()
    db.execute(
        """
        INSERT INTO iaquote_enrichments(product_id,status,updated_at)
        VALUES(?,'running',?)
        ON CONFLICT(product_id) DO UPDATE SET status='running', last_error='', updated_at=excluded.updated_at
        """,
        (product_id, now),
    )


def _process_item(path: str, job_id: int, item_id: int, product_id: int, client: CatalogueClient) -> str:
    with open_db(path) as db:
        product = db.execute("SELECT id, code, designation FROM products WHERE id=?", (product_id,)).fetchone()
        if not product:
            db.execute("UPDATE iaquote_job_items SET status='skipped', message='Produit absent', finished_at=? WHERE id=?", (utcnow(), item_id))
            db.commit()
            return "skipped"
        now = utcnow()
        db.execute("UPDATE iaquote_job_items SET status='running', started_at=? WHERE id=?", (now, item_id))
        _upsert_pending(db, product_id)
        db.commit()
    try:
        sheet = client.fetch(product["code"], product["designation"])
        now = utcnow()
        with open_db(path) as db:
            db.execute(
                """
                UPDATE iaquote_enrichments SET status='verified', source_url=?, source_title=?, match_score=?,
                    specs_json=?, last_error='', attempts=attempts+1, last_attempt_at=?, verified_at=?, updated_at=?
                WHERE product_id=?
                """,
                (sheet.url, sheet.title, sheet.match_score, json.dumps(sheet.specifications, ensure_ascii=False), now, now, now, product_id),
            )
            db.execute("UPDATE iaquote_job_items SET status='succeeded', message=?, finished_at=? WHERE id=?", (f"{len(sheet.specifications)} caractéristiques", now, item_id))
            db.commit()
        return "succeeded"
    except Exception as exc:
        message = str(exc)[:500]
        now = utcnow()
        with open_db(path) as db:
            db.execute(
                """
                UPDATE iaquote_enrichments SET status='failed', last_error=?, attempts=attempts+1,
                    last_attempt_at=?, updated_at=? WHERE product_id=?
                """,
                (message, now, now, product_id),
            )
            db.execute("UPDATE iaquote_job_items SET status='failed', message=?, finished_at=? WHERE id=?", (message, now, item_id))
            db.commit()
        return "failed"


def run_job(path: str, job_id: int, client: CatalogueClient, delay: float = 0.0) -> None:
    with _LOCK:
        if job_id in _RUNNING:
            return
        _RUNNING.add(job_id)
    try:
        with open_db(path) as db:
            job = db.execute("SELECT * FROM iaquote_jobs WHERE id=?", (job_id,)).fetchone()
            if not job or job["status"] == "completed":
                return
            now = utcnow()
            db.execute("UPDATE iaquote_jobs SET status='running', started_at=COALESCE(started_at,?), updated_at=? WHERE id=?", (now, now, job_id))
            db.execute("UPDATE iaquote_job_items SET status='pending', started_at=NULL WHERE job_id=? AND status='running'", (job_id,))
            db.commit()
        while True:
            with open_db(path) as db:
                item = db.execute(
                    "SELECT id, product_id FROM iaquote_job_items WHERE job_id=? AND status='pending' ORDER BY id LIMIT 1", (job_id,)
                ).fetchone()
            if not item:
                break
            _process_item(path, job_id, item["id"], item["product_id"], client)
            with open_db(path) as db:
                counts = {r["status"]: r["n"] for r in db.execute("SELECT status,COUNT(*) n FROM iaquote_job_items WHERE job_id=? GROUP BY status", (job_id,))}
                processed = counts.get("succeeded", 0) + counts.get("failed", 0) + counts.get("skipped", 0)
                db.execute(
                    "UPDATE iaquote_jobs SET processed=?, succeeded=?, failed=?, skipped=?, updated_at=? WHERE id=?",
                    (processed, counts.get("succeeded", 0), counts.get("failed", 0), counts.get("skipped", 0), utcnow(), job_id),
                )
                db.commit()
            if delay:
                time.sleep(delay)
        with open_db(path) as db:
            now = utcnow()
            db.execute("UPDATE iaquote_jobs SET status='completed', finished_at=?, updated_at=? WHERE id=?", (now, now, job_id))
            record_event(db, "enrichment.completed", f"Traitement #{job_id} terminé", {"job_id": job_id})
            db.commit()
    except Exception as exc:
        with open_db(path) as db:
            db.execute("UPDATE iaquote_jobs SET status='failed', last_error=?, finished_at=?, updated_at=? WHERE id=?", (str(exc)[:500], utcnow(), utcnow(), job_id))
            db.commit()
    finally:
        with _LOCK:
            _RUNNING.discard(job_id)


def launch_job(app, job_id: int) -> None:
    client = CatalogueClient(
        app.state.config["CATALOG_SEARCH_BASE_URL"],
        timeout=app.state.config["CATALOG_TIMEOUT"],
        threshold=app.state.config["CATALOG_MATCH_THRESHOLD"],
        delay=0,
    )
    executor = getattr(app.state, "enrichment_executor", None)
    if executor is None:
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="enrichment")
        app.state.enrichment_executor = executor
    executor.submit(run_job, app.state.config["DATABASE_PATH"], job_id, client, app.state.config["CATALOG_REQUEST_DELAY"])


def resume_interrupted(app) -> list[int]:
    resumed = []
    with open_db(app.state.config["DATABASE_PATH"]) as db:
        rows = db.execute("SELECT id FROM iaquote_jobs WHERE status IN ('queued','running') ORDER BY id").fetchall()
        db.execute("UPDATE iaquote_jobs SET status='queued', updated_at=? WHERE status='running'", (utcnow(),))
        db.execute("UPDATE iaquote_job_items SET status='pending', started_at=NULL WHERE status='running'")
        db.commit()
    for row in rows:
        launch_job(app, row["id"])
        resumed.append(row["id"])
    return resumed
