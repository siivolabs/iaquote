"""Flask application factory for IAQuote V0.3.2."""
from __future__ import annotations

import json
import os
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template, request, url_for

from . import catalog, enrichment, quotes
from .db import connect, database_path, init_db

VERSION = "0.3.2"


def _bootstrap(db_path: str) -> None:
    init_db(db_path)
    csv_path = os.getenv("BOOTSTRAP_CSV", "")
    if csv_path and Path(csv_path).is_file():
        with open(csv_path, "rb") as handle:
            catalog.import_csv(handle.read(), db_path)
    enrichment.seed_preverified(os.getenv("PRESEED_PATH", ""), db_path)
    enrichment.recover_interrupted_jobs(db_path)


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.from_mapping(
        SECRET_KEY=os.getenv("SECRET_KEY", "change-me-before-production"),
        DATABASE_PATH=os.getenv("DATABASE_PATH", "/data/devisia.sqlite"),
        TESTING=False,
        START_BACKGROUND_WORKER=os.getenv("START_BACKGROUND_WORKER", "1") == "1",
    )
    if test_config:
        app.config.update(test_config)
    db_path = database_path(app.config["DATABASE_PATH"])
    app.config["DATABASE_PATH"] = db_path
    _bootstrap(db_path)

    def _json_error(message: str, status: int = 400):
        return jsonify({"ok": False, "error": message}), status

    @app.context_processor
    def template_context():
        return {"version": VERSION}

    @app.get("/")
    @app.get("/dashboard")
    def dashboard():
        return render_template(
            "dashboard.html", stats=enrichment.catalog_stats(db_path),
            products=catalog.list_products(db_path, limit=5), quotes=quotes.list_quotes(db_path, 5),
        )

    @app.get("/catalog")
    def catalog_page():
        search = request.args.get("q", "")
        return render_template("catalog.html", products=catalog.list_products(db_path, search=search), search=search)

    @app.get("/enrichment")
    def enrichment_page():
        return render_template("enrichment.html", job=enrichment.get_bulk_job(db_path=db_path),
                               stats=enrichment.catalog_stats(db_path))

    @app.get("/history")
    def history_page():
        return render_template("history.html", quotes=quotes.list_quotes(db_path))

    @app.route("/quote", methods=["GET", "POST"])
    def quote_page():
        products = catalog.list_products(db_path)
        if request.method == "POST":
            try:
                created = quotes.create_quote({
                    "customer_name": request.form.get("customer_name", ""),
                    "kind": request.form.get("kind", "desktop"),
                    "codes": request.form.getlist("codes"),
                }, db_path)
                return redirect(url_for("quote_detail", quote_id=created["id"]))
            except ValueError as exc:
                return render_template("quote.html", products=products, error=str(exc)), 400
        return render_template("quote.html", products=products)

    @app.get("/quote/<int:quote_id>")
    def quote_detail(quote_id: int):
        item = quotes.get_quote(quote_id, db_path)
        if not item:
            return render_template("error.html", message="Devis introuvable"), 404
        return render_template("quote_detail.html", quote=item)

    @app.get("/api/health")
    def health():
        try:
            with connect(db_path) as con:
                con.execute("SELECT 1").fetchone()
            state = "ok"
        except Exception:
            state = "error"
        status = 200 if state == "ok" else 503
        return jsonify({"status": "ok" if state == "ok" else "degraded", "version": VERSION, "database": state}), status

    @app.get("/api/catalog")
    def api_catalog():
        return jsonify({"items": catalog.list_products(db_path, request.args.get("q", "")),
                        "stats": enrichment.catalog_stats(db_path)})

    @app.post("/api/catalog/import")
    def api_catalog_import():
        uploaded = request.files.get("file")
        if not uploaded:
            return _json_error("Fichier CSV manquant.")
        try:
            result = catalog.import_csv(uploaded.read(), db_path)
            job_id = enrichment.create_bulk_job(result["created_codes"], db_path, retry_failed=False)
            result["job_id"] = job_id
            if job_id and app.config["START_BACKGROUND_WORKER"] and not app.config["TESTING"]:
                enrichment.start_bulk_job(job_id, db_path)
            return jsonify({"ok": True, **result}), 201
        except ValueError as exc:
            return _json_error(str(exc))

    @app.post("/api/enrich/jobs")
    def api_create_job():
        payload = request.get_json(silent=True) or {}
        job_id = enrichment.create_bulk_job(payload.get("codes"), db_path, bool(payload.get("retry_failed", True)))
        if job_id is None:
            return jsonify({"ok": True, "created": False, "message": "Aucune référence à traiter."})
        if app.config["START_BACKGROUND_WORKER"] and not app.config["TESTING"]:
            enrichment.start_bulk_job(job_id, db_path)
        return jsonify({"ok": True, "created": True, "job": enrichment.get_bulk_job(job_id, db_path)}), 201

    @app.get("/api/enrich/jobs/current")
    def api_current_job():
        return jsonify({"job": enrichment.get_bulk_job(db_path=db_path), "catalog": enrichment.catalog_stats(db_path)})

    @app.get("/api/enrich/jobs/<int:job_id>")
    def api_job(job_id: int):
        job = enrichment.get_bulk_job(job_id, db_path)
        return (jsonify({"job": job}), 200) if job else _json_error("Tâche introuvable.", 404)

    @app.post("/api/enrich/jobs/<int:job_id>/run")
    def api_run_job(job_id: int):
        if not enrichment.get_bulk_job(job_id, db_path):
            return _json_error("Tâche introuvable.", 404)
        job = enrichment.run_bulk_job(job_id, db_path, delay=0 if app.config["TESTING"] else None)
        return jsonify({"ok": True, "job": job})

    @app.post("/api/enrich/manual")
    def api_manual_enrichment():
        payload = request.get_json(silent=True) or {}
        code = str(payload.get("code") or "").strip()
        if not code:
            return _json_error("Code article manquant.")
        enrichment.save_enrichment(code, payload, db_path)
        return jsonify({"ok": True}), 201

    @app.get("/api/quotes")
    def api_quotes():
        return jsonify({"items": quotes.list_quotes(db_path)})

    @app.post("/api/quotes")
    def api_create_quote():
        try:
            return jsonify({"ok": True, "quote": quotes.create_quote(request.get_json(silent=True) or {}, db_path)}), 201
        except ValueError as exc:
            return _json_error(str(exc))

    @app.get("/api/quotes/<int:quote_id>")
    def api_quote(quote_id: int):
        item = quotes.get_quote(quote_id, db_path)
        return (jsonify({"quote": item}), 200) if item else _json_error("Devis introuvable.", 404)

    @app.errorhandler(404)
    def not_found(_error):
        if request.path.startswith("/api/"):
            return _json_error("Ressource introuvable.", 404)
        return render_template("error.html", message="Page introuvable"), 404

    return app
