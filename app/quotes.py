"""Verified-data-only quotation service."""
from __future__ import annotations

import json
from typing import Any

from .db import table_columns, transaction, utcnow


def create_quote(payload: dict[str, Any], db_path: str | None = None) -> dict[str, Any]:
    requested = [str(code).strip() for code in payload.get("codes", []) if str(code).strip()]
    if not requested:
        raise ValueError("Sélectionnez au moins un article.")
    with transaction(db_path) as con:
        marks = ",".join("?" for _ in requested)
        rows = con.execute(
            f"""SELECT p.code,p.designation,p.price,p.stock,e.status,e.specs_json
                FROM products p LEFT JOIN enrichments e ON e.product_code=p.code
                WHERE p.code IN ({marks})""", requested,
        ).fetchall()
        by_code = {r["code"]: r for r in rows}
        missing = [code for code in requested if code not in by_code]
        unverified = [code for code in requested if code in by_code and by_code[code]["status"] != "verified"]
        if missing:
            raise ValueError("Articles absents du catalogue : " + ", ".join(missing))
        if unverified:
            raise ValueError("Fiches non vérifiées : " + ", ".join(unverified))
        items = [{
            "code": r["code"], "designation": r["designation"], "price": float(r["price"] or 0),
            "stock": int(r["stock"] or 0), "specs": json.loads(r["specs_json"] or "{}"),
        } for r in rows]
        total = round(sum(item["price"] for item in items), 2)
        now = utcnow()
        customer_name = str(payload.get("customer_name") or "").strip()
        kind = str(payload.get("kind") or "desktop")
        encoded_items = json.dumps(items, ensure_ascii=False)
        # V0.3.0/V0.3.1 quotes have additional NOT NULL columns. Supplying them
        # keeps new writes compatible without rebuilding or deleting the table.
        candidates = {
            "customer_name": customer_name,
            "kind": kind,
            "status": "draft",
            "total": total,
            "items_json": encoded_items,
            "created_at": now,
            "updated_at": now,
            "client_need": customer_name or "Devis IAQuote",
            "budget_ttc": total,
            "strategy": kind,
            "quote_json": encoded_items,
        }
        available = table_columns(con, "quotes")
        values = {name: value for name, value in candidates.items() if name in available}
        columns = ",".join(f'"{name}"' for name in values)
        placeholders = ",".join("?" for _ in values)
        cur = con.execute(
            f"INSERT INTO quotes({columns}) VALUES({placeholders})",
            tuple(values.values()),
        )
        quote_id = int(cur.lastrowid)
    return get_quote(quote_id, db_path) or {}


def get_quote(quote_id: int, db_path: str | None = None) -> dict[str, Any] | None:
    with transaction(db_path) as con:
        row = con.execute("SELECT * FROM quotes WHERE id=?", (quote_id,)).fetchone()
    if not row:
        return None
    result = dict(row)
    try:
        decoded = json.loads(result.pop("items_json") or "[]")
    except (TypeError, json.JSONDecodeError):
        decoded = []
    if isinstance(decoded, dict):
        decoded = decoded.get("items") or decoded.get("products") or []
    result["items"] = decoded if isinstance(decoded, list) else []
    return result


def list_quotes(db_path: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    with transaction(db_path) as con:
        rows = con.execute("SELECT * FROM quotes ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(row) for row in rows]
