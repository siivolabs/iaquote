"""SQLite access and transactional, additive schema migrations for IAQuote."""
from __future__ import annotations

import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

DEFAULT_DATABASE = "/data/devisia.sqlite"
SCHEMA_VERSION = 3
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


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


def _quoted(identifier: str) -> str:
    if not _IDENTIFIER.fullmatch(identifier):
        raise ValueError(f"Invalid SQLite identifier: {identifier!r}")
    return f'"{identifier}"'


def table_columns(con: sqlite3.Connection, table: str) -> set[str]:
    """Return a table's columns without relying on a historical primary key."""
    return {row["name"] for row in con.execute(f"PRAGMA table_info({_quoted(table)})")}


def _add_columns(con: sqlite3.Connection, table: str, definitions: dict[str, str]) -> None:
    existing = table_columns(con, table)
    for name, definition in definitions.items():
        if name not in existing:
            con.execute(
                f"ALTER TABLE {_quoted(table)} ADD COLUMN {_quoted(name)} {definition}"
            )
            existing.add(name)


def _create_baseline(con: sqlite3.Connection) -> None:
    """Create new-install tables one statement at a time to retain transactionality."""
    statements = (
        """CREATE TABLE IF NOT EXISTS products (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               code TEXT NOT NULL UNIQUE,
               designation TEXT NOT NULL,
               category TEXT DEFAULT '',
               price REAL DEFAULT 0,
               stock INTEGER DEFAULT 0,
               active INTEGER NOT NULL DEFAULT 1,
               created_at TEXT NOT NULL,
               updated_at TEXT NOT NULL
           )""",
        """CREATE TABLE IF NOT EXISTS enrichments (
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
           )""",
        """CREATE TABLE IF NOT EXISTS enrichment_jobs (
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
           )""",
        """CREATE TABLE IF NOT EXISTS enrichment_job_items (
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
           )""",
        """CREATE TABLE IF NOT EXISTS quotes (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               customer_name TEXT DEFAULT '',
               kind TEXT NOT NULL DEFAULT 'desktop',
               status TEXT NOT NULL DEFAULT 'draft',
               total REAL NOT NULL DEFAULT 0,
               items_json TEXT NOT NULL DEFAULT '[]',
               created_at TEXT NOT NULL,
               updated_at TEXT NOT NULL
           )""",
        """CREATE TABLE IF NOT EXISTS iaquote_schema_migrations (
               version INTEGER PRIMARY KEY,
               name TEXT NOT NULL,
               applied_at TEXT NOT NULL
           )""",
    )
    for statement in statements:
        con.execute(statement)


def _migration_1_add_current_columns(con: sqlite3.Connection) -> None:
    """Add every column used by V0.3.2 while retaining all legacy columns/rows."""
    _add_columns(
        con,
        "products",
        {
            "category": "TEXT DEFAULT ''",
            "price": "REAL DEFAULT 0",
            "stock": "INTEGER DEFAULT 0",
            "active": "INTEGER NOT NULL DEFAULT 1",
            "created_at": "TEXT",
            "updated_at": "TEXT",
        },
    )
    _add_columns(
        con,
        "enrichments",
        {
            "source_url": "TEXT DEFAULT ''",
            "source_title": "TEXT",
            "status": "TEXT NOT NULL DEFAULT 'review'",
            "confidence": "REAL NOT NULL DEFAULT 0",
            "specs_json": "TEXT NOT NULL DEFAULT '{}'",
            "raw_summary": "TEXT",
            "validation_note": "TEXT",
            "error": "TEXT",
            "checked_at": "TEXT",
            "fetched_at": "TEXT",
            "verified_at": "TEXT",
            "updated_at": "TEXT",
        },
    )
    _add_columns(
        con,
        "enrichment_jobs",
        {
            "status": "TEXT NOT NULL DEFAULT 'pending'",
            "total": "INTEGER NOT NULL DEFAULT 0",
            "processed": "INTEGER NOT NULL DEFAULT 0",
            "verified": "INTEGER NOT NULL DEFAULT 0",
            "review": "INTEGER NOT NULL DEFAULT 0",
            "failed": "INTEGER NOT NULL DEFAULT 0",
            "created_at": "TEXT",
            "started_at": "TEXT",
            "finished_at": "TEXT",
            # Add nullable first: SQLite cannot add a non-constant timestamp default.
            "updated_at": "TEXT",
        },
    )
    _add_columns(
        con,
        "enrichment_job_items",
        {
            "status": "TEXT NOT NULL DEFAULT 'pending'",
            "error": "TEXT",
            "attempts": "INTEGER NOT NULL DEFAULT 0",
            "started_at": "TEXT",
            "finished_at": "TEXT",
        },
    )
    _add_columns(
        con,
        "quotes",
        {
            "customer_name": "TEXT DEFAULT ''",
            "kind": "TEXT NOT NULL DEFAULT 'desktop'",
            "status": "TEXT NOT NULL DEFAULT 'draft'",
            "total": "REAL NOT NULL DEFAULT 0",
            "items_json": "TEXT NOT NULL DEFAULT '[]'",
            "created_at": "TEXT",
            "updated_at": "TEXT",
        },
    )


def _migration_2_backfill_legacy_values(con: sqlite3.Connection) -> None:
    """Populate added columns from their V0.3.0/V0.3.1 equivalents."""
    now = utcnow()
    product_columns = table_columns(con, "products")
    created_source = "imported_at" if "imported_at" in product_columns else "NULL"
    updated_sources = ["updated_at"]
    for candidate in ("source_updated_at", "imported_at", "created_at"):
        if candidate in product_columns:
            updated_sources.append(candidate)
    updated_sources.append("?")
    con.execute(
        f"""UPDATE products
            SET created_at=COALESCE(NULLIF(created_at,''), {created_source}, ?),
                updated_at=COALESCE({', '.join(updated_sources)}),
                category=COALESCE(category,''), price=COALESCE(price,0),
                stock=COALESCE(stock,0), active=COALESCE(active,1)""",
        (now, now),
    )
    if "sale_price_ttc" in product_columns:
        con.execute("UPDATE products SET price=sale_price_ttc WHERE price=0 AND sale_price_ttc IS NOT NULL")
    if "stock_shop" in product_columns and "stock_central" in product_columns:
        con.execute(
            """UPDATE products SET stock=COALESCE(stock_shop,0)+COALESCE(stock_central,0)
               WHERE stock=0 AND (COALESCE(stock_shop,0)<>0 OR COALESCE(stock_central,0)<>0)"""
        )
    if "obsolete" in product_columns:
        con.execute("UPDATE products SET active=CASE WHEN COALESCE(obsolete,0)=0 THEN 1 ELSE 0 END")

    con.execute(
        """UPDATE enrichments
           SET source_url=COALESCE(source_url,''), status=COALESCE(NULLIF(status,''),'review'),
               confidence=COALESCE(confidence,0), specs_json=COALESCE(NULLIF(specs_json,''),'{}'),
               fetched_at=COALESCE(fetched_at,checked_at,?),
               checked_at=COALESCE(checked_at,fetched_at,?),
               updated_at=COALESCE(updated_at,verified_at,fetched_at,checked_at,?)""",
        (now, now, now),
    )

    con.execute(
        """UPDATE enrichment_jobs
           SET status=COALESCE(NULLIF(status,''),'pending'), total=COALESCE(total,0),
               processed=COALESCE(processed,0), verified=COALESCE(verified,0),
               review=COALESCE(review,0), failed=COALESCE(failed,0),
               created_at=COALESCE(created_at,started_at,finished_at,?),
               updated_at=COALESCE(updated_at,finished_at,started_at,created_at,?)""",
        (now, now),
    )

    item_columns = table_columns(con, "enrichment_job_items")
    finished_source = "processed_at" if "processed_at" in item_columns else "NULL"
    con.execute(
        f"""UPDATE enrichment_job_items
            SET status=COALESCE(NULLIF(status,''),'pending'), attempts=COALESCE(attempts,0),
                finished_at=COALESCE(finished_at,{finished_source})"""
    )
    con.execute(
        """UPDATE enrichment_job_items SET attempts=1
           WHERE attempts=0 AND status IN ('verified','review','failed','not_found','cancelled')"""
    )

    quote_columns = table_columns(con, "quotes")
    total_source = "budget_ttc" if "budget_ttc" in quote_columns else "0"
    items_source = "quote_json" if "quote_json" in quote_columns else "'[]'"
    con.execute(
        f"""UPDATE quotes
            SET customer_name=COALESCE(customer_name,''), kind=COALESCE(NULLIF(kind,''),'desktop'),
                status=COALESCE(NULLIF(status,''),'draft'),
                total=CASE WHEN total IS NULL OR total=0 THEN COALESCE({total_source},0) ELSE total END,
                items_json=CASE WHEN items_json IS NULL OR items_json='' OR items_json='[]'
                                THEN COALESCE({items_source},'[]') ELSE items_json END,
                created_at=COALESCE(created_at,?), updated_at=COALESCE(updated_at,created_at,?)""",
        (now, now),
    )


def _migration_3_indexes(con: sqlite3.Connection) -> None:
    for statement in (
        "CREATE INDEX IF NOT EXISTS idx_products_designation ON products(designation)",
        "CREATE INDEX IF NOT EXISTS idx_job_items_status ON enrichment_job_items(job_id, status)",
        "CREATE INDEX IF NOT EXISTS idx_jobs_status ON enrichment_jobs(status, id)",
    ):
        con.execute(statement)


MIGRATIONS = (
    (1, "add-current-columns", _migration_1_add_current_columns),
    (2, "backfill-legacy-values", _migration_2_backfill_legacy_values),
    (3, "ensure-indexes", _migration_3_indexes),
)


def init_db(path: str | None = None) -> str:
    """Create/migrate the schema atomically; never drop a table, column, or row."""
    db = database_path(path)
    with transaction(db) as con:
        _create_baseline(con)
        applied = {
            int(row["version"])
            for row in con.execute("SELECT version FROM iaquote_schema_migrations")
        }
        for version, name, migration in MIGRATIONS:
            if version in applied:
                continue
            migration(con)
            con.execute(
                "INSERT INTO iaquote_schema_migrations(version,name,applied_at) VALUES(?,?,?)",
                (version, name, utcnow()),
            )
        con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    return db


def insert_product(
    con: sqlite3.Connection,
    code: str,
    designation: str,
    *,
    category: str = "",
    price: float = 0,
    stock: int = 0,
    now: str | None = None,
) -> None:
    """Insert into either the current products table or a migrated legacy one."""
    timestamp = now or utcnow()
    available = table_columns(con, "products")
    candidates: dict[str, Any] = {
        "code": code,
        "designation": designation,
        "category": category,
        "price": price,
        "stock": stock,
        "active": 1,
        "created_at": timestamp,
        "updated_at": timestamp,
        # Required by V0.3.0/V0.3.1 tables and harmless when absent.
        "imported_at": timestamp,
        "sale_price_ttc": price,
        "stock_shop": stock,
        "obsolete": 0,
    }
    values = {name: value for name, value in candidates.items() if name in available}
    columns = ",".join(_quoted(name) for name in values)
    placeholders = ",".join("?" for _ in values)
    con.execute(
        f"INSERT INTO products({columns}) VALUES({placeholders})",
        tuple(values.values()),
    )
