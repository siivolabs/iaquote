import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from app.catalog_client import CatalogueClient
from app.db import catalogue_stats, import_catalogue, open_db
from app.enrichment import create_job, run_job

PRODUCTS = {
    "S0106238": ("G.Skill Aegis 32 Go (2 x 16 Go) DDR4 2666 MHz CL19", "PB00258014", {"Capacité": "32 Go", "Fréquence": "2666 MHz"}),
    "S0350964": ("Eaton 3S 850 FR (Gen 2)", "PB00309549", {"Puissance": "850 VA", "Prises": "8"}),
    "S0872743": ("ICY BOX IB-233U3-B", "PB00213003", {"Interface": "USB 3.0", "Format": "2.5 pouces"}),
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/recherche/"):
            code = self.path.rsplit("/", 1)[-1]
            product = PRODUCTS.get(code)
            if not product:
                self.send_response(404); self.end_headers(); return
            self.send_response(302); self.send_header("Location", f"/fiche/{product[1]}.html"); self.end_headers(); return
        if self.path.startswith("/fiche/"):
            product = next((v for v in PRODUCTS.values() if f"/{v[1]}.html" in self.path), None)
            if not product:
                self.send_response(404); self.end_headers(); return
            rows = "".join(f"<tr><th>{k}</th><td>{v}</td></tr>" for k, v in product[2].items())
            body = f"<html><head><title>{product[0]}</title></head><body><h1>{product[0]}</h1><table>{rows}</table></body></html>".encode()
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
        self.send_response(404); self.end_headers()

    def log_message(self, *_args):
        pass


@pytest.fixture
def catalogue_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/recherche/{{code}}"
    finally:
        server.shutdown(); thread.join()


@pytest.mark.e2e
def test_real_reference_pipeline_search_to_sqlite(app, catalogue_server):
    csv_lines = ["code;designation"] + [f"{code};{data[0]}" for code, data in PRODUCTS.items()]
    with open_db(app.state.config["DATABASE_PATH"]) as db:
        import_catalogue(db, "\n".join(csv_lines).encode())
    job_id = create_job(app.state.config["DATABASE_PATH"], "missing_or_failed")
    run_job(app.state.config["DATABASE_PATH"], job_id, CatalogueClient(catalogue_server, threshold=.58), 0)
    with open_db(app.state.config["DATABASE_PATH"]) as db:
        job = db.execute("SELECT * FROM iaquote_jobs WHERE id=?", (job_id,)).fetchone()
        rows = db.execute("SELECT * FROM iaquote_enrichments ORDER BY product_id").fetchall()
        assert job["status"] == "completed" and job["succeeded"] == 3 and job["failed"] == 0
        assert all(r["status"] == "verified" and json.loads(r["specs_json"]) for r in rows)
        assert catalogue_stats(db)["coverage"] == 100.0


@pytest.mark.e2e
def test_failed_reference_is_requeued_and_recovered(app, catalogue_server):
    with open_db(app.state.config["DATABASE_PATH"]) as db:
        import_catalogue(db, b"code;designation\nBAD;Produit absent\n")
    first = create_job(app.state.config["DATABASE_PATH"], "missing_or_failed")
    run_job(app.state.config["DATABASE_PATH"], first, CatalogueClient(catalogue_server, threshold=.5), 0)
    with open_db(app.state.config["DATABASE_PATH"]) as db:
        assert db.execute("SELECT status FROM iaquote_enrichments").fetchone()["status"] == "failed"
        product_id = db.execute("SELECT id FROM products").fetchone()["id"]
        db.execute("UPDATE products SET code='S0872743', designation='ICY BOX IB-233U3-B' WHERE id=?", (product_id,)); db.commit()
    retry = create_job(app.state.config["DATABASE_PATH"], "failed")
    run_job(app.state.config["DATABASE_PATH"], retry, CatalogueClient(catalogue_server, threshold=.5), 0)
    with open_db(app.state.config["DATABASE_PATH"]) as db:
        row = db.execute("SELECT status, attempts FROM iaquote_enrichments").fetchone()
        assert row["status"] == "verified" and row["attempts"] == 2
