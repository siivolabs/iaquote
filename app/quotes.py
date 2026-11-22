"""Verified-data-only quotation service."""
from __future__ import annotations

import json
from typing import Any

from .db import transaction, utcnow


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
        cur = con.execute(
            """INSERT INTO quotes(customer_name,kind,status,total,items_json,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?)""",
            (str(payload.get("customer_name") or "").strip(), str(payload.get("kind") or "desktop"),
             "draft", total, json.dumps(items, ensure_ascii=False), now, now),
        )
        quote_id = int(cur.lastrowid)
    return get_quote(quote_id, db_path) or {}


def get_quote(quote_id: int, db_path: str | None = None) -> dict[str, Any] | None:
    with transaction(db_path) as con:
        row = con.execute("SELECT * FROM quotes WHERE id=?", (quote_id,)).fetchone()
    if not row:
        return None
    result = dict(row)
    result["items"] = json.loads(result.pop("items_json") or "[]")
    return result


def list_quotes(db_path: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    with transaction(db_path) as con:
        rows = con.execute("SELECT * FROM quotes ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(row) for row in rows]
