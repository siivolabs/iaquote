from __future__ import annotations

import os
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .db import import_catalogue, init_schema, open_db

VERSION = "0.3.2"


def create_app(test_config: dict | None = None) -> FastAPI:
    config = {
        "TESTING": False,
        "SECRET_KEY": os.getenv("SECRET_KEY", "development-only-change-me"),
        "DATABASE_PATH": os.getenv("DATABASE_PATH", "/data/devisia.sqlite"),
        "CATALOG_SEARCH_BASE_URL": os.getenv("CATALOG_SEARCH_BASE_URL", ""),
        "CATALOG_MATCH_THRESHOLD": float(os.getenv("CATALOG_MATCH_THRESHOLD", "0.58")),
        "CATALOG_TIMEOUT": float(os.getenv("CATALOG_TIMEOUT", "25")),
        "CATALOG_REQUEST_DELAY": float(os.getenv("CATALOG_REQUEST_DELAY", "1.0")),
        "BOOTSTRAP_CSV": os.getenv("BOOTSTRAP_CSV", ""),
        "VERSION": VERSION,
    }
    if test_config:
        config.update(test_config)
    app = FastAPI(title="IAQuote", version=VERSION, docs_url=None, redoc_url=None)
    app.state.config = config
    app.add_middleware(SessionMiddleware, secret_key=config["SECRET_KEY"], same_site="lax", https_only=False)
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
    init_schema(config["DATABASE_PATH"])
    bootstrap = config.get("BOOTSTRAP_CSV")
    if bootstrap and Path(bootstrap).is_file():
        with open_db(config["DATABASE_PATH"]) as db:
            if db.execute("SELECT COUNT(*) n FROM products").fetchone()["n"] == 0:
                import_catalogue(db, Path(bootstrap).read_bytes())
    from .routes import register_routes
    register_routes(app)
    if not config["TESTING"] and config["CATALOG_SEARCH_BASE_URL"]:
        from .enrichment import resume_interrupted
        resume_interrupted(app)
    return app
