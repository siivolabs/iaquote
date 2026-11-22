"""SQLite access and additive schema migrations for IAQuote."""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

DEFAULT_DATABASE = "/data/devisia.sqlite"


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def database_path(explicit: str | None = None) -> str:
    path = explicit or os.getenv("DATABASE_PATH", DEFAULT_DATABASE)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    return path


def connect(path: str | None = None) -> sqlite3.Connection:
    con = sqlite3.connect(database_path(path), timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA busy_timeout = 30000")
    return con


@contextmanager
def transaction(path: str | None = None) -> Iterator[sqlite3.Connection]:
    con = connect(path)
    try:
        con.execute("BEGIN IMMEDIATE")
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def _columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in con.execute(f"PRAGMA table_info({table})")}


def _add_columns(con: sqlite3.Connection, table: str, definitions: dict[str, str]) -> None:
    existing = _columns(con, table)
    for name, definition in definitions.items():
        if name not in existing:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def init_db(path: str | None = None) -> str:
    """Create or add to the schema. Existing tables and rows are never removed."""
    db = database_path(path)
    with transaction(db) as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL UNIQUE,
                designation TEXT NOT NULL,
                category TEXT DEFAULT '',
                price REAL DEFAULT 0,
                stock INTEGER DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS enrichments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_code TEXT NOT NULL UNIQUE,
                source_url TEXT NOT NULL DEFAULT '',
                source_title TEXT,
                status TEXT NOT NULL DEFAULT 'review',
                confidence REAL NOT NULL DEFAULT 0,
                specs_json TEXT NOT NULL DEFAULT '{}',
                raw_summary TEXT,
                validation_note TEXT,
                error TEXT,
                checked_at TEXT,
                fetched_at TEXT,
                verified_at TEXT,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(product_code) REFERENCES products(code)
                    ON UPDATE CASCADE ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS enrichment_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                status TEXT NOT NULL DEFAULT 'pending',
                total INTEGER NOT NULL DEFAULT 0,
                processed INTEGER NOT NULL DEFAULT 0,
                verified INTEGER NOT NULL DEFAULT 0,
                review INTEGER NOT NULL DEFAULT 0,
                failed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS enrichment_job_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                product_code TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                error TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                started_at TEXT,
                finished_at TEXT,
                UNIQUE(job_id, product_code),
                FOREIGN KEY(job_id) REFERENCES enrichment_jobs(id) ON DELETE CASCADE,
                FOREIGN KEY(product_code) REFERENCES products(code)
                    ON UPDATE CASCADE ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS quotes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_name TEXT DEFAULT '',
                kind TEXT NOT NULL DEFAULT 'desktop',
                status TEXT NOT NULL DEFAULT 'draft',
                total REAL NOT NULL DEFAULT 0,
                items_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_products_designation
                ON products(designation);
            CREATE INDEX IF NOT EXISTS idx_job_items_status
                ON enrichment_job_items(job_id, status);
            CREATE INDEX IF NOT EXISTS idx_jobs_status
                ON enrichment_jobs(status, id);
            """
        )
        # Additive compatibility with databases created by V0.3.x variants.
        _add_columns(con, "products", {
            "category": "TEXT DEFAULT ''", "price": "REAL DEFAULT 0",
            "stock": "INTEGER DEFAULT 0", "active": "INTEGER NOT NULL DEFAULT 1",
            "created_at": "TEXT", "updated_at": "TEXT",
        })
        _add_columns(con, "enrichments", {
            "source_url": "TEXT NOT NULL DEFAULT ''", "source_title": "TEXT",
            "status": "TEXT NOT NULL DEFAULT 'review'", "confidence": "REAL NOT NULL DEFAULT 0",
            "specs_json": "TEXT NOT NULL DEFAULT '{}'", "raw_summary": "TEXT",
            "validation_note": "TEXT", "error": "TEXT", "checked_at": "TEXT",
            "fetched_at": "TEXT", "verified_at": "TEXT", "updated_at": "TEXT",
        })
    return db
