"""Catalogue import and query helpers."""
from __future__ import annotations

import csv
import io
import re
from typing import Any, Iterable

from .db import transaction, utcnow

HEADER_ALIASES = {
    "code": {"code", "code article", "reference", "référence", "ref", "sku"},
    "designation": {"designation", "désignation", "produit", "libelle", "libellé", "name", "nom"},
    "category": {"category", "catégorie", "categorie", "famille", "rayon"},
    "price": {"price", "prix", "prix ttc", "prix ht", "tarif"},
    "stock": {"stock", "quantite", "quantité", "qty"},
}


def _normal(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _find_header(headers: list[str], canonical: str) -> str | None:
    aliases = HEADER_ALIASES[canonical]
    return next((h for h in headers if _normal(h) in aliases), None)


def _number(value: Any, integer: bool = False) -> float | int:
    text = str(value or "0").strip().replace("\u00a0", "").replace("€", "").replace(",", ".")
    try:
        return int(float(text)) if integer else float(text)
    except ValueError:
        return 0 if integer else 0.0


def parse_csv(content: bytes | str) -> list[dict[str, Any]]:
    if isinstance(content, bytes):
        text = content.decode("utf-8-sig", errors="replace")
    else:
        text = content.lstrip("\ufeff")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t|")
    except csv.Error:
        dialect = csv.excel
        dialect.delimiter = ";"
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    headers = [h or "" for h in (reader.fieldnames or [])]
    code_h = _find_header(headers, "code")
    designation_h = _find_header(headers, "designation")
    if not code_h or not designation_h:
        raise ValueError("Le CSV doit contenir une colonne code/référence et une colonne désignation/produit.")
    category_h = _find_header(headers, "category")
    price_h = _find_header(headers, "price")
    stock_h = _find_header(headers, "stock")
    rows: list[dict[str, Any]] = []
    for raw in reader:
        code = str(raw.get(code_h) or "").strip()
        designation = str(raw.get(designation_h) or "").strip()
        if not code or not designation:
            continue
        rows.append({
            "code": code,
            "designation": designation,
            "category": str(raw.get(category_h) or "").strip() if category_h else "",
            "price": _number(raw.get(price_h)) if price_h else 0.0,
            "stock": _number(raw.get(stock_h), integer=True) if stock_h else 0,
        })
    return rows


def import_products(rows: Iterable[dict[str, Any]], db_path: str | None = None) -> dict[str, Any]:
    now = utcnow()
    created: list[str] = []
    updated = 0
    with transaction(db_path) as con:
        for item in rows:
            code = str(item.get("code") or "").strip()
            designation = str(item.get("designation") or "").strip()
            if not code or not designation:
                continue
            existing = con.execute("SELECT id FROM products WHERE code = ?", (code,)).fetchone()
            if existing:
                con.execute(
                    """UPDATE products SET designation=?, category=?, price=?, stock=?, active=1,
                       updated_at=? WHERE code=?""",
                    (designation, item.get("category", ""), _number(item.get("price")),
                     _number(item.get("stock"), integer=True), now, code),
                )
                updated += 1
            else:
                con.execute(
                    """INSERT INTO products(code,designation,category,price,stock,active,created_at,updated_at)
                       VALUES(?,?,?,?,?,1,?,?)""",
                    (code, designation, item.get("category", ""), _number(item.get("price")),
                     _number(item.get("stock"), integer=True), now, now),
                )
                created.append(code)
    return {"created": len(created), "updated": updated, "created_codes": created}


def import_csv(content: bytes | str, db_path: str | None = None) -> dict[str, Any]:
    rows = parse_csv(content)
    result = import_products(rows, db_path)
    result["read"] = len(rows)
    return result


def list_products(db_path: str | None = None, search: str = "", limit: int = 1000) -> list[dict[str, Any]]:
    with transaction(db_path) as con:
        if search:
            token = f"%{search.strip()}%"
            rows = con.execute(
                """SELECT p.*, e.status AS enrichment_status, e.confidence, e.source_url
                   FROM products p LEFT JOIN enrichments e ON e.product_code=p.code
                   WHERE p.code LIKE ? OR p.designation LIKE ? ORDER BY p.designation LIMIT ?""",
                (token, token, limit),
            ).fetchall()
        else:
            rows = con.execute(
                """SELECT p.*, e.status AS enrichment_status, e.confidence, e.source_url
                   FROM products p LEFT JOIN enrichments e ON e.product_code=p.code
                   ORDER BY p.designation LIMIT ?""", (limit,),
            ).fetchall()
    return [dict(row) for row in rows]
