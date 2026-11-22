from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable



def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=30000")
    return db



@contextmanager
def open_db(path: str):
    db = connect(path)
    try:
        yield db
    finally:
        db.close()


def _columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in db.execute(f'PRAGMA table_info("{table}")')}


def _ensure_columns(db: sqlite3.Connection, table: str, definitions: dict[str, str]) -> None:
    existing = _columns(db, table)
    for name, definition in definitions.items():
        if name not in existing:
            db.execute(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {definition}')


def init_schema(path: str) -> None:
    with open_db(path) as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL,
                designation TEXT NOT NULL,
                brand TEXT DEFAULT '',
                price REAL DEFAULT 0,
                stock INTEGER DEFAULT 0,
                created_at TEXT,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS iaquote_schema (
                version INTEGER NOT NULL,
                applied_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS iaquote_enrichments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'missing',
                source_url TEXT DEFAULT '',
                source_title TEXT DEFAULT '',
                match_score REAL DEFAULT 0,
                specs_json TEXT DEFAULT '{}',
                last_error TEXT DEFAULT '',
                attempts INTEGER DEFAULT 0,
                last_attempt_at TEXT,
                verified_at TEXT,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS iaquote_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                total INTEGER NOT NULL DEFAULT 0,
                processed INTEGER NOT NULL DEFAULT 0,
                succeeded INTEGER NOT NULL DEFAULT 0,
                failed INTEGER NOT NULL DEFAULT 0,
                skipped INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                updated_at TEXT NOT NULL,
                last_error TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS iaquote_job_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                message TEXT DEFAULT '',
                started_at TEXT,
                finished_at TEXT,
                UNIQUE(job_id, product_id),
                FOREIGN KEY(job_id) REFERENCES iaquote_jobs(id) ON DELETE CASCADE,
                FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS iaquote_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                message TEXT NOT NULL,
                details_json TEXT DEFAULT '{}',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS quotes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                quote_number TEXT NOT NULL UNIQUE,
                customer_name TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'draft',
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS quote_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                quote_id INTEGER NOT NULL,
                product_id INTEGER,
                designation TEXT NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 1,
                unit_price REAL NOT NULL DEFAULT 0,
                FOREIGN KEY(quote_id) REFERENCES quotes(id) ON DELETE CASCADE,
                FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS idx_enrichments_status ON iaquote_enrichments(status);
            CREATE INDEX IF NOT EXISTS idx_jobs_status ON iaquote_jobs(status);
            CREATE INDEX IF NOT EXISTS idx_job_items_status ON iaquote_job_items(job_id, status);
            CREATE INDEX IF NOT EXISTS idx_products_code_lookup ON products(code);
            """
        )
        _ensure_columns(
            db,
            "products",
            {
                "code": "TEXT DEFAULT ''",
                "designation": "TEXT DEFAULT ''",
                "brand": "TEXT DEFAULT ''",
                "price": "REAL DEFAULT 0",
                "stock": "INTEGER DEFAULT 0",
                "created_at": "TEXT",
                "updated_at": "TEXT",
            },
        )
        _ensure_columns(
            db,
            "quotes",
            {
                "quote_number": "TEXT",
                "customer_name": "TEXT DEFAULT ''",
                "status": "TEXT NOT NULL DEFAULT 'draft'",
                "notes": "TEXT DEFAULT ''",
                "created_at": "TEXT",
                "updated_at": "TEXT",
            },
        )
        _ensure_columns(
            db,
            "quote_items",
            {
                "quote_id": "INTEGER",
                "product_id": "INTEGER",
                "designation": "TEXT DEFAULT ''",
                "quantity": "INTEGER NOT NULL DEFAULT 1",
                "unit_price": "REAL NOT NULL DEFAULT 0",
            },
        )
        now = utcnow()
        db.execute("UPDATE products SET created_at=COALESCE(created_at, ?), updated_at=COALESCE(updated_at, ?)", (now, now))
        db.execute("UPDATE quotes SET quote_number=COALESCE(NULLIF(quote_number,''), printf('DEV-LEGACY-%06d', id)), created_at=COALESCE(created_at, ?), updated_at=COALESCE(updated_at, ?)", (now, now))
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_quotes_number ON quotes(quote_number)")
        db.execute("INSERT INTO iaquote_schema(version, applied_at) SELECT 3, ? WHERE NOT EXISTS (SELECT 1 FROM iaquote_schema WHERE version=3)", (now,))
        db.commit()


def record_event(db: sqlite3.Connection, event_type: str, message: str, details=None) -> None:
    db.execute(
        "INSERT INTO iaquote_events(event_type, message, details_json, created_at) VALUES(?,?,?,?)",
        (event_type, message, json.dumps(details or {}, ensure_ascii=False), utcnow()),
    )


def _normalise_header(value: str) -> str:
    return "".join(c for c in value.strip().lower().replace(" ", "_") if c.isalnum() or c == "_")


def _pick(row: dict[str, str], names: Iterable[str], default=""):
    for name in names:
        if name in row and str(row[name]).strip():
            return str(row[name]).strip()
    return default


def _price(value: str) -> float:
    cleaned = str(value or "0").replace("\u202f", "").replace(" ", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _integer(value: str) -> int:
    try:
        return int(float(str(value or "0").replace(",", ".")))
    except ValueError:
        return 0


def import_catalogue(db: sqlite3.Connection, content: bytes) -> dict[str, int]:
    text = content.decode("utf-8-sig", errors="replace")
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t,")
    except csv.Error:
        dialect = csv.excel
        dialect.delimiter = ";"
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        raise ValueError("Le fichier CSV ne contient pas d'en-tête.")
    reader.fieldnames = [_normalise_header(h) for h in reader.fieldnames]
    created = updated = ignored = 0
    now = utcnow()
    for raw in reader:
        row = {_normalise_header(k): (v or "") for k, v in raw.items() if k}
        code = _pick(row, ("code", "reference", "ref", "sku", "code_article", "article"))
        designation = _pick(row, ("designation", "description", "produit", "libelle", "name", "nom"))
        if not code or not designation:
            ignored += 1
            continue
        brand = _pick(row, ("brand", "marque"))
        price = _price(_pick(row, ("prix", "price", "prix_ht", "unit_price"), "0"))
        stock = _integer(_pick(row, ("stock", "quantite", "qty", "quantity"), "0"))
        existing = db.execute("SELECT id FROM products WHERE code=? ORDER BY id LIMIT 1", (code,)).fetchone()
        if existing:
            db.execute(
                "UPDATE products SET designation=?, brand=?, price=?, stock=?, updated_at=? WHERE id=?",
                (designation, brand, price, stock, now, existing["id"]),
            )
            updated += 1
        else:
            db.execute(
                "INSERT INTO products(code, designation, brand, price, stock, created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
                (code, designation, brand, price, stock, now, now),
            )
            created += 1
    record_event(db, "catalogue.import", "Catalogue importé", {"created": created, "updated": updated, "ignored": ignored})
    db.commit()
    return {"created": created, "updated": updated, "ignored": ignored, "total": created + updated}


def catalogue_stats(db: sqlite3.Connection) -> dict:
    total = db.execute("SELECT COUNT(*) n FROM products").fetchone()["n"]
    counts = {row["status"]: row["n"] for row in db.execute("SELECT status, COUNT(*) n FROM iaquote_enrichments GROUP BY status")}
    verified = counts.get("verified", 0)
    failed = counts.get("failed", 0)
    pending = counts.get("pending", 0) + counts.get("running", 0)
    missing = max(0, total - sum(counts.values())) + counts.get("missing", 0)
    return {
        "total": total,
        "verified": verified,
        "failed": failed,
        "pending": pending,
        "missing": missing,
        "coverage": round((verified / total * 100), 1) if total else 0,
    }


def create_quote(db: sqlite3.Connection, customer_name: str, items: list[dict], notes: str = "") -> int:
    now = utcnow()
    sequence = db.execute("SELECT COALESCE(MAX(id), 0) + 1 n FROM quotes").fetchone()["n"]
    number = f"DEV-{datetime.now():%Y%m%d}-{sequence:04d}"
    cur = db.execute(
        "INSERT INTO quotes(quote_number, customer_name, notes, created_at, updated_at) VALUES(?,?,?,?,?)",
        (number, customer_name.strip(), notes.strip(), now, now),
    )
    quote_id = cur.lastrowid
    for item in items:
        product_id = int(item["product_id"])
        product = db.execute("SELECT designation, price FROM products WHERE id=?", (product_id,)).fetchone()
        if not product:
            continue
        quantity = max(1, int(item.get("quantity", 1)))
        db.execute(
            "INSERT INTO quote_items(quote_id, product_id, designation, quantity, unit_price) VALUES(?,?,?,?,?)",
            (quote_id, product_id, product["designation"], quantity, float(product["price"] or 0)),
        )
    record_event(db, "quote.created", f"Devis {number} créé", {"quote_id": quote_id})
    db.commit()
    return quote_id
