import json
from dataclasses import dataclass

from app.db import transaction, utcnow
from app.enrichment import (
    catalog_stats, create_bulk_job, direct_search_url, extract_specs, fetch_direct, get_bulk_job,
    recover_interrupted_jobs, run_bulk_job, save_enrichment, title_match_score, title_matches,
)


def test_direct_url_encodes_code(monkeypatch):
    monkeypatch.setenv("CATALOG_SEARCH_BASE_URL", "https://catalog.example/search/{code}")
    assert direct_search_url("A B/1") == "https://catalog.example/search/A%20B%2F1"


def test_direct_url_without_placeholder():
    assert direct_search_url("ABC", "https://catalog.example/search") == "https://catalog.example/search/ABC"


def test_title_validation():
    ok, score = title_matches("Intel Core Ultra 5 225 (3.3 GHz)", "Intel Core Ultra 5 225 - Processeur")
    assert ok and score >= 0.58
    assert title_match_score("Intel Core Ultra 5", "Câble audio Jack") < 0.2


def test_extract_table_and_definition_specs():
    html = "<table><tr><th>Socket</th><td>AM5</td></tr></table><dl><dt>Mémoire</dt><dd>DDR5</dd></dl>"
    assert extract_specs(html) == {"Socket": "AM5", "Mémoire": "DDR5"}


def test_fetch_direct_uses_article_code_redirect_and_extracts_specs(monkeypatch):
    monkeypatch.setenv("CATALOG_SEARCH_BASE_URL", "https://catalog.example/recherche/{code}")

    @dataclass
    class FakeResponse:
        url: str = "https://catalog.example/fiche/intel-core-ultra-5-225"
        text: str = """<html><head><title>Intel Core Ultra 5 225 - Processeur</title></head>
            <body><table><tr><th>Socket</th><td>LGA1851</td></tr></table></body></html>"""

        def raise_for_status(self):
            return None

    class FakeSession:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return FakeResponse()

    session = FakeSession()
    result = fetch_direct("S0111966", "Intel Core Ultra 5 225", session=session)

    assert session.calls[0][0] == "https://catalog.example/recherche/S0111966"
    assert session.calls[0][1]["allow_redirects"] is True
    assert result["status"] == "verified"
    assert result["source_url"].endswith("/fiche/intel-core-ultra-5-225")
    assert result["specs"] == {"Socket": "LGA1851"}


def test_save_preverified_without_source_url(app):
    save_enrichment("NO-URL", {"designation": "Seed", "status": "verified", "specs": {"x": 1}}, app.config["DATABASE_PATH"])
    with transaction(app.config["DATABASE_PATH"]) as con:
        row = con.execute("SELECT source_url,status,fetched_at FROM enrichments WHERE product_code='NO-URL'").fetchone()
    assert row["source_url"] == "" and row["status"] == "verified" and row["fetched_at"]


def test_bootstrap_preverified_is_visible(app):
    stats = catalog_stats(app.config["DATABASE_PATH"])
    assert stats == {"total": 4, "verified": 4, "review": 0, "failed": 0, "remaining": 0}


def test_no_empty_job_is_created(app):
    assert create_bulk_job(db_path=app.config["DATABASE_PATH"], retry_failed=False) is None


def test_create_job_for_new_product(app):
    now = utcnow()
    with transaction(app.config["DATABASE_PATH"]) as con:
        con.execute("INSERT INTO products(code,designation,created_at,updated_at) VALUES('PENDING','Pending',?,?)", (now, now))
    job_id = create_bulk_job(["PENDING"], app.config["DATABASE_PATH"])
    job = get_bulk_job(job_id, app.config["DATABASE_PATH"])
    assert job["total"] == 1 and job["status"] == "pending"


def test_bulk_worker_continues_after_one_product_failure(app):
    now = utcnow()
    with transaction(app.config["DATABASE_PATH"]) as con:
        con.execute("INSERT INTO products(code,designation,created_at,updated_at) VALUES('FAIL','Bad',?,?)", (now, now))
        con.execute("INSERT INTO products(code,designation,created_at,updated_at) VALUES('GOOD','Good',?,?)", (now, now))
    job_id = create_bulk_job(["FAIL", "GOOD"], app.config["DATABASE_PATH"])
    def fetcher(code, designation):
        if code == "FAIL":
            raise RuntimeError("network")
        return {"status": "verified", "source_url": "https://x/fiche/1", "source_title": designation, "confidence": 1, "specs": {}}
    job = run_bulk_job(job_id, app.config["DATABASE_PATH"], fetcher=fetcher, delay=0)
    assert job["processed"] == 2 and job["verified"] == 1 and job["failed"] == 1


def test_recover_interrupted_job(app):
    now = utcnow()
    with transaction(app.config["DATABASE_PATH"]) as con:
        con.execute("INSERT INTO products(code,designation,created_at,updated_at) VALUES('RECOVER','Recover',?,?)", (now, now))
    job_id = create_bulk_job(["RECOVER"], app.config["DATABASE_PATH"])
    with transaction(app.config["DATABASE_PATH"]) as con:
        con.execute("UPDATE enrichment_jobs SET status='running' WHERE id=?", (job_id,))
        con.execute("UPDATE enrichment_job_items SET status='running' WHERE job_id=?", (job_id,))
    assert recover_interrupted_jobs(app.config["DATABASE_PATH"]) == 1
    assert get_bulk_job(job_id, app.config["DATABASE_PATH"])["status"] == "pending"


def test_manual_enrichment_api(client):
    response = client.post("/api/enrich/manual", json={"code": "MANUAL", "designation": "Manual", "status": "verified", "specs": {"a": "b"}})
    assert response.status_code == 201


def test_current_job_api_contains_catalog(client):
    body = client.get("/api/enrich/jobs/current").get_json()
    assert body["catalog"]["total"] == 4
