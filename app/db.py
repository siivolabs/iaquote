import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def database_path():
    return os.getenv("DATABASE_PATH", "/data/devisia.sqlite")


def connect(path=None):
    path = path or database_path()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    con = sqlite3.connect(path, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA journal_mode=WAL")
    return con


@contextmanager
def transaction(path=None):
    con = connect(path)
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init_db(path=None):
    with transaction(path) as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS products (
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
            CREATE INDEX IF NOT EXISTS idx_products_category ON products(category);
            CREATE INDEX IF NOT EXISTS idx_products_designation ON products(designation);
            CREATE INDEX IF NOT EXISTS idx_products_active ON products(obsolete, stock_central, stock_shop);

            CREATE TABLE IF NOT EXISTS enrichments (
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

            CREATE TABLE IF NOT EXISTS quotes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                client_need TEXT NOT NULL,
                budget_ttc REAL NOT NULL,
                strategy TEXT NOT NULL,
                quote_json TEXT NOT NULL
            );
            """
        )


def row_to_dict(row):
    return dict(row) if row is not None else None


def product_with_enrichment(con, code):
    row = con.execute(
        """SELECT p.*, e.source_url, e.source_title, e.status AS enrichment_status,
                  e.confidence, e.specs_json, e.validation_note, e.fetched_at
           FROM products p LEFT JOIN enrichments e ON e.product_code=p.code
           WHERE p.code=?""",
        (code,),
    ).fetchone()
    if not row:
        return None
    item = dict(row)
    item["specs"] = json.loads(item.pop("specs_json") or "{}")
    return item
