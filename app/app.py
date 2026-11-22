import json
import os
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from . import ollama
from .db import connect, init_db, product_with_enrichment, row_to_dict, transaction
from .enrichment import (
    cancel_bulk_enrichment,
    enrich_product,
    get_bulk_job,
    queue_bulk_enrichment,
    resume_bulk_enrichment,
    seed_preverified,
)
from .importer import import_csv_bytes, import_csv_file
from .quote_engine import generate_quote


def _bootstrap(db_path):
    init_db(db_path)
    with transaction(db_path) as con:
        count = con.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    csv_path = os.getenv("BOOTSTRAP_CSV", "").strip()
    if count == 0 and csv_path and Path(csv_path).is_file():
        import_csv_file(csv_path, db_path)
    seed_preverified(os.getenv("PRESEED_PATH", ""), db_path)
    resume_bulk_enrichment(db_path)


def _env_enabled(name, default=True):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "oui", "on"}


def create_app(test_config=None):
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.update(
        SECRET_KEY=os.getenv("SECRET_KEY") or os.urandom(32),
        MAX_CONTENT_LENGTH=20 * 1024 * 1024,
        DATABASE_PATH=os.getenv("DATABASE_PATH", "/data/devisia.sqlite"),
    )
    if test_config:
        app.config.update(test_config)
    db_path = app.config["DATABASE_PATH"]
    _bootstrap(db_path)

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/health")
    def health():
        with transaction(db_path) as con:
            product_count = con.execute("SELECT COUNT(*) FROM products").fetchone()[0]
            enriched_count = con.execute("SELECT COUNT(*) FROM enrichments WHERE status='verified'").fetchone()[0]
            quote_count = con.execute("SELECT COUNT(*) FROM quotes").fetchone()[0]
        result = {"ok": True, "version": "0.3.0", "catalog_count": product_count, "verified_count": enriched_count, "quote_count": quote_count}
        job = get_bulk_job(db_path=db_path)
        if job:
            result["enrichment_job"] = job
        if request.args.get("deep") == "1":
            result["ollama"] = ollama.health()
        return jsonify(result)

    @app.get("/api/ollama/test")
    def ollama_test():
        return jsonify(ollama.health())

    @app.post("/api/import")
    def import_catalog():
        if "file" not in request.files:
            return jsonify({"error": "Fichier CSV manquant"}), 400
        upload = request.files["file"]
        if not upload.filename.lower().endswith(".csv"):
            return jsonify({"error": "Le fichier doit être au format CSV"}), 400
        try:
            result = import_csv_bytes(upload.read(), db_path)
            enrichment_codes = result.pop("enrichment_codes", [])
            result["preseeded"] = seed_preverified(os.getenv("PRESEED_PATH", ""), db_path)
            if _env_enabled("AUTO_ENRICH_ON_IMPORT", True):
                job = queue_bulk_enrichment(
                    enrichment_codes,
                    trigger="import",
                    db_path=db_path,
                )
                result["enrichment_job"] = job
                result["queued_for_enrichment"] = job["total"]
            else:
                result["queued_for_enrichment"] = 0
                result["auto_enrichment"] = "disabled"
            return jsonify(result)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.get("/api/catalog")
    def catalog():
        q = request.args.get("q", "").strip()
        category = request.args.get("category", "").strip()
        status = request.args.get("status", "").strip()
        page = max(1, request.args.get("page", 1, type=int))
        per_page = min(100, max(10, request.args.get("per_page", 30, type=int)))
        where, params = ["1=1"], []
        if q:
            where.append("(p.code LIKE ? OR p.designation LIKE ? OR p.brand LIKE ?)")
            token = f"%{q}%"
            params.extend([token, token, token])
        if category:
            where.append("p.category=?")
            params.append(category)
        if status == "verified":
            where.append("e.status='verified'")
        elif status == "missing":
            where.append("e.product_code IS NULL")
        elif status == "review":
            where.append("e.status='review'")
        clause = " AND ".join(where)
        with transaction(db_path) as con:
            total = con.execute(f"SELECT COUNT(*) FROM products p LEFT JOIN enrichments e ON e.product_code=p.code WHERE {clause}", params).fetchone()[0]
            rows = con.execute(
                f"""SELECT p.*, e.status AS enrichment_status, e.confidence, e.source_url, e.validation_note
                    FROM products p LEFT JOIN enrichments e ON e.product_code=p.code
                    WHERE {clause} ORDER BY p.category, p.designation LIMIT ? OFFSET ?""",
                params + [per_page, (page - 1) * per_page],
            ).fetchall()
            categories = [r[0] for r in con.execute("SELECT DISTINCT category FROM products WHERE category<>'' ORDER BY category").fetchall()]
        return jsonify({"items": [dict(r) for r in rows], "total": total, "page": page, "per_page": per_page, "categories": categories})

    @app.get("/api/catalog/<code>")
    def catalog_item(code):
        with transaction(db_path) as con:
            item = product_with_enrichment(con, code)
        if not item:
            return jsonify({"error": "Référence introuvable"}), 404
        return jsonify(item)

    @app.post("/api/enrich/<code>")
    def enrich(code):
        body = request.get_json(silent=True) or {}
        try:
            item = enrich_product(code, body.get("url"), bool(body.get("force")), db_path)
            return jsonify(item)
        except KeyError as exc:
            return jsonify({"error": str(exc)}), 404
        except (ValueError, RuntimeError) as exc:
            return jsonify({"error": str(exc), "hint": "Vous pouvez fournir l'adresse exacte de la fiche technique."}), 400
        except Exception as exc:
            return jsonify({"error": f"Échec de l'enrichissement technique : {exc}", "hint": "La source peut refuser les requêtes automatisées. Réessayez plus tard ou saisissez l'adresse exacte."}), 502

    @app.post("/api/enrich/bulk")
    def enrich_bulk():
        body = request.get_json(silent=True) or {}
        job = queue_bulk_enrichment(
            force=bool(body.get("force")),
            trigger="manual",
            db_path=db_path,
        )
        return jsonify(job), 202 if job["status"] in {"queued", "running"} else 200

    @app.get("/api/enrich/jobs/current")
    def enrichment_job_current():
        return jsonify({"job": get_bulk_job(db_path=db_path)})

    @app.get("/api/enrich/jobs/<int:job_id>")
    def enrichment_job(job_id):
        job = get_bulk_job(job_id, db_path)
        if not job:
            return jsonify({"error": "Tâche d'enrichissement introuvable"}), 404
        return jsonify(job)

    @app.post("/api/enrich/jobs/<int:job_id>/cancel")
    def enrichment_job_cancel(job_id):
        try:
            return jsonify(cancel_bulk_enrichment(job_id, db_path))
        except KeyError as exc:
            return jsonify({"error": str(exc)}), 404

    @app.get("/api/enrichments")
    def enrichments():
        with transaction(db_path) as con:
            rows = con.execute(
                """SELECT p.code, p.designation, p.category, e.status, e.confidence,
                          e.source_url, e.validation_note, e.fetched_at
                   FROM enrichments e JOIN products p ON p.code=e.product_code
                   ORDER BY e.fetched_at DESC"""
            ).fetchall()
        return jsonify({"items": [dict(r) for r in rows]})

    @app.post("/api/quotes")
    def create_quote():
        body = request.get_json(silent=True) or {}
        need = str(body.get("client_need", "")).strip()
        if len(need) < 10:
            return jsonify({"error": "Décrivez le besoin client en au moins 10 caractères."}), 400
        try:
            budget = float(body.get("budget_ttc", 0))
        except (TypeError, ValueError):
            budget = 0
        if budget <= 0:
            return jsonify({"error": "Le budget TTC doit être supérieur à zéro."}), 400
        try:
            quote = generate_quote(
                need, budget, str(body.get("strategy", "balanced")),
                str(body.get("machine_type", "auto")), bool(body.get("verified_only", True)), db_path,
            )
            return jsonify(quote), 201
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.get("/api/quotes")
    def quotes():
        with transaction(db_path) as con:
            rows = con.execute("SELECT id, created_at, client_need, budget_ttc, strategy, quote_json FROM quotes ORDER BY id DESC LIMIT 100").fetchall()
        items = []
        for row in rows:
            quote = json.loads(row["quote_json"])
            items.append({"id": row["id"], "created_at": row["created_at"], "client_need": row["client_need"], "budget_ttc": row["budget_ttc"], "strategy": row["strategy"], "total_ttc": quote.get("total_ttc"), "ai": quote.get("ai")})
        return jsonify({"items": items})

    @app.get("/api/quotes/<int:quote_id>")
    def quote_detail(quote_id):
        with transaction(db_path) as con:
            row = con.execute("SELECT quote_json FROM quotes WHERE id=?", (quote_id,)).fetchone()
        if not row:
            return jsonify({"error": "Devis introuvable"}), 404
        return jsonify(json.loads(row[0]))

    @app.errorhandler(413)
    def too_large(_):
        return jsonify({"error": "Fichier trop volumineux (20 Mo maximum)."}), 413

    return app


if __name__ == "__main__":
    create_app().run(host="0.0.0.0", port=8000, debug=False)
