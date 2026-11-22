from __future__ import annotations

import json
import math
import sqlite3
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .db import catalogue_stats, create_quote, import_catalogue, open_db
from .enrichment import create_job, launch_job

templates = Jinja2Templates(directory=str(__import__("pathlib").Path(__file__).parent / "templates"))


def _config(request: Request) -> dict:
    return request.app.state.config


def _flash(request: Request, message: str, category: str = "success") -> None:
    request.session.setdefault("messages", []).append((category, message))


def _context(request: Request, **values):
    endpoint = request.scope.get("route").name if request.scope.get("route") else ""
    return {"request": request, "config": SimpleNamespace(**_config(request)), "endpoint": endpoint,
            "messages": request.session.pop("messages", []), **values}


def _redirect(request: Request, route: str, **params):
    return RedirectResponse(str(request.url_for(route, **params)), status_code=303)


def _latest_job(db):
    return db.execute("SELECT * FROM iaquote_jobs ORDER BY id DESC LIMIT 1").fetchone()


def register_routes(app: FastAPI) -> None:
    @app.get("/", response_class=HTMLResponse, name="dashboard")
    async def dashboard(request: Request):
        with open_db(_config(request)["DATABASE_PATH"]) as db:
            stats = catalogue_stats(db)
            recent_quotes = db.execute(
                """SELECT q.*, COALESCE(SUM(i.quantity*i.unit_price),0) total
                   FROM quotes q LEFT JOIN quote_items i ON i.quote_id=q.id
                   GROUP BY q.id ORDER BY q.id DESC LIMIT 5"""
            ).fetchall()
            events = db.execute("SELECT * FROM iaquote_events ORDER BY id DESC LIMIT 8").fetchall()
            job = _latest_job(db)
        return templates.TemplateResponse(request=request, name="dashboard.html", context=_context(request, stats=stats, job=job, quotes=recent_quotes, events=events))

    @app.api_route("/catalogue", methods=["GET", "POST"], response_class=HTMLResponse, name="catalogue")
    async def catalogue(request: Request):
        path = _config(request)["DATABASE_PATH"]
        if request.method == "POST":
            form = await request.form()
            uploaded = form.get("catalogue")
            if not uploaded or not getattr(uploaded, "filename", ""):
                _flash(request, "Sélectionnez un fichier CSV.", "error")
                return _redirect(request, "catalogue")
            try:
                with open_db(path) as db:
                    result = import_catalogue(db, await uploaded.read())
                _flash(request, f"Import terminé : {result['created']} créé(s), {result['updated']} mis à jour, {result['ignored']} ignoré(s).")
                if form.get("enqueue") == "yes" and result["total"]:
                    job_id = create_job(path, "missing_or_failed")
                    if _config(request)["CATALOG_SEARCH_BASE_URL"]:
                        launch_job(request.app, job_id)
                        _flash(request, f"Enrichissement #{job_id} lancé.")
            except (ValueError, UnicodeError) as exc:
                _flash(request, str(exc), "error")
            return _redirect(request, "catalogue")
        q = request.query_params.get("q", "").strip()
        try:
            page = max(1, int(request.query_params.get("page", "1")))
        except ValueError:
            page = 1
        per_page, where, args = 50, "", []
        if q:
            where, args = "WHERE p.code LIKE ? OR p.designation LIKE ?", [f"%{q}%", f"%{q}%"]
        with open_db(path) as db:
            total = db.execute(f"SELECT COUNT(*) n FROM products p {where}", args).fetchone()["n"]
            products = db.execute(
                f"""SELECT p.*, e.status enrichment_status, e.match_score, e.source_url, e.last_error, e.specs_json
                     FROM products p LEFT JOIN iaquote_enrichments e ON e.product_id=p.id
                     {where} ORDER BY p.designation LIMIT ? OFFSET ?""", [*args, per_page, (page - 1) * per_page]
            ).fetchall()
        return templates.TemplateResponse(request=request, name="catalogue.html", context=_context(request, products=products, q=q, page=page, pages=max(1, math.ceil(total / per_page)), total=total))

    @app.get("/catalogue/{product_id:int}", response_class=HTMLResponse, name="product_detail")
    async def product_detail(request: Request, product_id: int):
        with open_db(_config(request)["DATABASE_PATH"]) as db:
            product = db.execute("SELECT p.*, e.status, e.source_url, e.source_title, e.match_score, e.specs_json, e.last_error, e.attempts, e.last_attempt_at, e.verified_at FROM products p LEFT JOIN iaquote_enrichments e ON e.product_id=p.id WHERE p.id=?", (product_id,)).fetchone()
        if not product:
            return HTMLResponse("Not found", status_code=404)
        specs = json.loads(product["specs_json"] or "{}")
        return templates.TemplateResponse(request=request, name="product.html", context=_context(request, product=product, specs=specs))

    @app.get("/enrichissement", response_class=HTMLResponse, name="enrichment")
    async def enrichment(request: Request):
        with open_db(_config(request)["DATABASE_PATH"]) as db:
            job = _latest_job(db)
            rows = db.execute(
                """SELECT p.id, p.code, p.designation, e.status, e.match_score, e.source_url,
                          e.source_title, e.last_error, e.attempts, e.updated_at
                   FROM products p LEFT JOIN iaquote_enrichments e ON e.product_id=p.id
                   ORDER BY CASE COALESCE(e.status,'missing') WHEN 'running' THEN 0 WHEN 'failed' THEN 1 WHEN 'missing' THEN 2 ELSE 3 END,
                            p.designation LIMIT 200"""
            ).fetchall()
            stats = catalogue_stats(db)
        return templates.TemplateResponse(request=request, name="enrichment.html", context=_context(request, stats=stats, job=job, rows=rows, configured=bool(_config(request)["CATALOG_SEARCH_BASE_URL"])))

    @app.post("/enrichissement/lancer", name="enrichment_start")
    async def enrichment_start(request: Request):
        config = _config(request)
        if not config["CATALOG_SEARCH_BASE_URL"]:
            _flash(request, "Configurez CATALOG_SEARCH_BASE_URL avant de lancer l'enrichissement.", "error")
            return _redirect(request, "enrichment")
        form = await request.form()
        try:
            job_id = create_job(config["DATABASE_PATH"], str(form.get("mode", "missing_or_failed")))
            launch_job(request.app, job_id)
            _flash(request, f"Traitement #{job_id} lancé.")
        except ValueError as exc:
            _flash(request, str(exc), "error")
        return _redirect(request, "enrichment")

    @app.post("/enrichissement/produit/{product_id:int}", name="enrichment_one")
    async def enrichment_one(request: Request, product_id: int):
        config = _config(request)
        if not config["CATALOG_SEARCH_BASE_URL"]:
            _flash(request, "L'endpoint de recherche n'est pas configuré.", "error")
        else:
            job_id = create_job(config["DATABASE_PATH"], "all", [product_id])
            launch_job(request.app, job_id)
            _flash(request, f"Référence placée dans le traitement #{job_id}.")
        return RedirectResponse(request.headers.get("referer") or str(request.url_for("enrichment")), status_code=303)

    @app.get("/devis", response_class=HTMLResponse, name="quotes")
    async def quotes(request: Request):
        with open_db(_config(request)["DATABASE_PATH"]) as db:
            rows = db.execute(
                """SELECT q.*, COALESCE(SUM(i.quantity*i.unit_price),0) total, COUNT(i.id) lines
                   FROM quotes q LEFT JOIN quote_items i ON i.quote_id=q.id GROUP BY q.id ORDER BY q.id DESC"""
            ).fetchall()
        return templates.TemplateResponse(request=request, name="quotes.html", context=_context(request, quotes=rows))

    @app.api_route("/devis/nouveau", methods=["GET", "POST"], response_class=HTMLResponse, name="quote_new")
    async def quote_new(request: Request):
        path = _config(request)["DATABASE_PATH"]
        if request.method == "POST":
            form = await request.form()
            ids, quantities = form.getlist("product_id"), form.getlist("quantity")
            items = [{"product_id": pid, "quantity": quantities[i] if i < len(quantities) else 1} for i, pid in enumerate(ids) if pid]
            if not items:
                _flash(request, "Ajoutez au moins un produit.", "error")
            else:
                with open_db(path) as db:
                    quote_id = create_quote(db, str(form.get("customer_name", "")), items, str(form.get("notes", "")))
                _flash(request, "Devis créé.")
                return _redirect(request, "quote_detail", quote_id=str(quote_id))
        with open_db(path) as db:
            products = db.execute("SELECT id, code, designation, price FROM products ORDER BY designation").fetchall()
        return templates.TemplateResponse(request=request, name="quote_new.html", context=_context(request, products=products))

    @app.get("/devis/{quote_id:int}", response_class=HTMLResponse, name="quote_detail")
    async def quote_detail(request: Request, quote_id: int):
        with open_db(_config(request)["DATABASE_PATH"]) as db:
            quote_row = db.execute("SELECT * FROM quotes WHERE id=?", (quote_id,)).fetchone()
            if not quote_row:
                return HTMLResponse("Not found", status_code=404)
            items = db.execute("SELECT *, quantity*unit_price line_total FROM quote_items WHERE quote_id=? ORDER BY id", (quote_id,)).fetchall()
        return templates.TemplateResponse(request=request, name="quote_detail.html", context=_context(request, quote=quote_row, items=items, total=sum(item["line_total"] for item in items)))

    @app.get("/historique", response_class=HTMLResponse, name="history")
    async def history(request: Request):
        with open_db(_config(request)["DATABASE_PATH"]) as db:
            events = db.execute("SELECT * FROM iaquote_events ORDER BY id DESC LIMIT 300").fetchall()
            jobs = db.execute("SELECT * FROM iaquote_jobs ORDER BY id DESC LIMIT 100").fetchall()
        return templates.TemplateResponse(request=request, name="history.html", context=_context(request, events=events, jobs=jobs))

    @app.get("/api/health", name="api_health")
    async def api_health(request: Request):
        try:
            with open_db(_config(request)["DATABASE_PATH"]) as db:
                db.execute("SELECT 1").fetchone()
            database = "ok"
        except sqlite3.Error:
            database = "error"
        return {"status": "ok" if database == "ok" else "degraded", "version": _config(request)["VERSION"], "database": database}

    @app.get("/api/stats", name="api_stats")
    async def api_stats(request: Request):
        with open_db(_config(request)["DATABASE_PATH"]) as db:
            job = _latest_job(db)
            return {"catalogue": catalogue_stats(db), "latest_job": dict(job) if job else None}

    @app.get("/api/jobs/{job_id:int}", name="api_job")
    async def api_job(request: Request, job_id: int):
        with open_db(_config(request)["DATABASE_PATH"]) as db:
            job = db.execute("SELECT * FROM iaquote_jobs WHERE id=?", (job_id,)).fetchone()
            if not job:
                return JSONResponse({"error": "not_found"}, status_code=404)
            data = dict(job)
            data["progress"] = round(data["processed"] / data["total"] * 100, 1) if data["total"] else 100
            data["items"] = [dict(row) for row in db.execute(
                """SELECT i.status, i.message, p.code, p.designation FROM iaquote_job_items i
                   JOIN products p ON p.id=i.product_id WHERE i.job_id=? ORDER BY i.id DESC LIMIT 100""", (job_id,)
            )]
        return data
