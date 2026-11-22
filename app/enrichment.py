"""Direct article-code enrichment, extraction, persistence and resilient jobs."""
from __future__ import annotations

import json
import os
import re
import threading
import time
import unicodedata
from difflib import SequenceMatcher
from html import unescape
from typing import Any, Callable, Iterable
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

from .db import insert_product, table_columns, transaction, utcnow

DEFAULT_SEARCH_URL = ""
ACTIVE = ("pending", "queued", "running", "blocked", "cancelling")


def direct_search_url(code: str, base_url: str | None = None) -> str:
    template = base_url or os.getenv("CATALOG_SEARCH_BASE_URL", DEFAULT_SEARCH_URL)
    if not template:
        raise RuntimeError("CATALOG_SEARCH_BASE_URL doit être configurée avec le marqueur {code}.")
    encoded = quote(str(code).strip(), safe="")
    return template.replace("{code}", encoded) if "{code}" in template else template.rstrip("/") + "/" + encoded


def _words(value: str) -> list[str]:
    folded = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode().lower()
    return [w for w in re.findall(r"[a-z0-9]+", folded) if len(w) > 1 and w not in {"produit", "fiche"}]


def title_match_score(designation: str, title: str) -> float:
    wanted, found = _words(designation), _words(title)
    if not wanted or not found:
        return 0.0
    overlap = len(set(wanted) & set(found)) / len(set(wanted))
    sequence = SequenceMatcher(None, " ".join(wanted), " ".join(found)).ratio()
    return round((overlap * 0.72) + (sequence * 0.28), 4)


def title_matches(designation: str, title: str, threshold: float | None = None) -> tuple[bool, float]:
    score = title_match_score(designation, title)
    minimum = threshold if threshold is not None else float(os.getenv("ENRICHMENT_MATCH_THRESHOLD", "0.58"))
    return score >= minimum, score


def extract_specs(html: str) -> dict[str, str]:
    soup = BeautifulSoup(html or "", "html.parser")
    specs: dict[str, str] = {}
    for row in soup.select("table tr"):
        cells = [c.get_text(" ", strip=True) for c in row.select("th,td")]
        if len(cells) >= 2 and cells[0] and cells[1]:
            specs[cells[0]] = " ".join(cells[1:])
    for term in soup.select("dt"):
        value = term.find_next_sibling("dd")
        if value:
            key, val = term.get_text(" ", strip=True), value.get_text(" ", strip=True)
            if key and val:
                specs[key] = val
    for item in soup.select(".specification li, .specifications li, .technical-data li, [class*='caracter'] li"):
        text = item.get_text(" ", strip=True)
        if ":" in text:
            key, value = text.split(":", 1)
            if key.strip() and value.strip():
                specs.setdefault(key.strip(), value.strip())
    return dict(list(specs.items())[:250])


def fetch_direct(code: str, designation: str, session: requests.Session | None = None) -> dict[str, Any]:
    client = session or requests.Session()
    headers = {"User-Agent": os.getenv("HTTP_USER_AGENT", "IAQuote/0.3.2 (+catalog-enrichment)")}
    url = direct_search_url(code)
    response = client.get(url, timeout=float(os.getenv("HTTP_TIMEOUT", "20")), allow_redirects=True, headers=headers)
    response.raise_for_status()
    final_url = str(response.url)
    soup = BeautifulSoup(response.text, "html.parser")
    title = unescape(soup.title.get_text(" ", strip=True)) if soup.title else ""
    matched, score = title_matches(designation, title)
    if "/fiche/" not in final_url:
        return {"source_url": final_url, "source_title": title, "status": "not_found",
                "confidence": 0.0, "specs": {}, "validation_note": "Aucune redirection vers une fiche produit."}
    specs = extract_specs(response.text)
    status = "verified" if matched else "review"
    return {
        "source_url": final_url, "source_title": title, "status": status,
        "confidence": score, "specs": specs,
        "raw_summary": " | ".join(f"{k}: {v}" for k, v in list(specs.items())[:20]),
        "validation_note": "Correspondance validée." if matched else "Titre à contrôler avant utilisation.",
    }


def save_enrichment(code: str, result: dict[str, Any], db_path: str | None = None) -> None:
    """Upsert enrichment, including preverified records without a source URL."""
    now = utcnow()
    source_url = str(result.get("source_url") or "")
    status = str(result.get("status") or "review")
    with transaction(db_path) as con:
        if not con.execute("SELECT 1 FROM products WHERE code=?", (code,)).fetchone():
            insert_product(con, code, str(result.get("designation") or code), now=now)
        values = (
            code, source_url, result.get("source_title"), status, float(result.get("confidence") or 0),
            json.dumps(result.get("specs") or {}, ensure_ascii=False), result.get("raw_summary"),
            result.get("validation_note"), result.get("error"), now, now,
            now if status == "verified" else None, now,
        )
        con.execute(
            """INSERT INTO enrichments(product_code,source_url,source_title,status,confidence,specs_json,
               raw_summary,validation_note,error,checked_at,fetched_at,verified_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(product_code) DO UPDATE SET
                 source_url=excluded.source_url, source_title=excluded.source_title,
                 status=excluded.status, confidence=excluded.confidence,
                 specs_json=excluded.specs_json, raw_summary=excluded.raw_summary,
                 validation_note=excluded.validation_note, error=excluded.error,
                 checked_at=excluded.checked_at, fetched_at=excluded.fetched_at,
                 verified_at=CASE WHEN excluded.status='verified' THEN excluded.verified_at ELSE enrichments.verified_at END,
                 updated_at=excluded.updated_at""", values,
        )


def seed_preverified(path: str, db_path: str | None = None) -> int:
    if not path or not os.path.exists(path):
        return 0
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    items = payload.get("items", payload) if isinstance(payload, dict) else payload
    count = 0
    for item in items:
        code = str(item.get("code") or item.get("product_code") or "").strip()
        if code:
            save_enrichment(code, item, db_path)
            count += 1
    return count


def _eligible_codes(con, codes: Iterable[str] | None, retry_failed: bool) -> list[str]:
    if codes is not None:
        clean = list(dict.fromkeys(str(c).strip() for c in codes if str(c).strip()))
        if not clean:
            return []
        marks = ",".join("?" for _ in clean)
        return [r["code"] for r in con.execute(f"SELECT code FROM products WHERE code IN ({marks})", clean)]
    where = "e.product_code IS NULL"
    if retry_failed:
        where += " OR e.status IN ('failed','not_found','review')"
    return [r["code"] for r in con.execute(
        f"SELECT p.code FROM products p LEFT JOIN enrichments e ON e.product_code=p.code WHERE {where} ORDER BY p.code"
    )]


def create_bulk_job(codes: Iterable[str] | None = None, db_path: str | None = None,
                    retry_failed: bool = True) -> int | None:
    now = utcnow()
    with transaction(db_path) as con:
        selected = _eligible_codes(con, codes, retry_failed)
        if not selected:
            active = con.execute(
                "SELECT id FROM enrichment_jobs WHERE status IN ('pending','queued','running','blocked','cancelling') ORDER BY id DESC LIMIT 1"
            ).fetchone()
            return int(active["id"]) if active else None
        active = con.execute(
            "SELECT id FROM enrichment_jobs WHERE status IN ('pending','queued','running','blocked','cancelling') ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if active:
            job_id = int(active["id"])
            for code in selected:
                con.execute(
                    "INSERT OR IGNORE INTO enrichment_job_items(job_id,product_code,status) VALUES(?,?,'pending')",
                    (job_id, code),
                )
            total = con.execute("SELECT COUNT(*) n FROM enrichment_job_items WHERE job_id=?", (job_id,)).fetchone()["n"]
            con.execute("UPDATE enrichment_jobs SET total=?, status='pending', updated_at=? WHERE id=?", (total, now, job_id))
            return job_id
        cur = con.execute(
            "INSERT INTO enrichment_jobs(status,total,created_at,updated_at) VALUES('pending',?,?,?)",
            (len(selected), now, now),
        )
        job_id = int(cur.lastrowid)
        con.executemany(
            "INSERT INTO enrichment_job_items(job_id,product_code,status) VALUES(?,?,'pending')",
            [(job_id, code) for code in selected],
        )
        return job_id


def recover_interrupted_jobs(db_path: str | None = None) -> int:
    now = utcnow()
    with transaction(db_path) as con:
        rows = con.execute("SELECT id FROM enrichment_jobs WHERE status IN ('running','blocked','cancelling')").fetchall()
        for row in rows:
            con.execute(
                "UPDATE enrichment_job_items SET status='pending', started_at=NULL WHERE job_id=? AND status='running'",
                (row["id"],),
            )
            con.execute("UPDATE enrichment_jobs SET status='pending', updated_at=? WHERE id=?", (now, row["id"]))
    return len(rows)


def _refresh_job(con, job_id: int) -> None:
    counts = {row["status"]: row["n"] for row in con.execute(
        "SELECT status, COUNT(*) n FROM enrichment_job_items WHERE job_id=? GROUP BY status", (job_id,)
    )}
    total = sum(counts.values())
    processed = sum(counts.get(k, 0) for k in ("verified", "review", "failed", "not_found"))
    final = processed >= total and total > 0
    con.execute(
        """UPDATE enrichment_jobs SET total=?, processed=?, verified=?, review=?, failed=?,
           status=?, finished_at=CASE WHEN ? THEN ? ELSE finished_at END, updated_at=? WHERE id=?""",
        (total, processed, counts.get("verified", 0), counts.get("review", 0),
         counts.get("failed", 0) + counts.get("not_found", 0), "finished" if final else "running",
         1 if final else 0, utcnow(), utcnow(), job_id),
    )


def run_bulk_job(job_id: int, db_path: str | None = None,
                 fetcher: Callable[[str, str], dict[str, Any]] = fetch_direct,
                 delay: float | None = None) -> dict[str, Any]:
    pause = float(os.getenv("ENRICHMENT_DELAY_SECONDS", "2.5")) if delay is None else delay
    with transaction(db_path) as con:
        con.execute(
            "UPDATE enrichment_jobs SET status='running', started_at=COALESCE(started_at,?), updated_at=? WHERE id=?",
            (utcnow(), utcnow(), job_id),
        )
    while True:
        with transaction(db_path) as con:
            item = con.execute(
                """SELECT i.product_code,p.designation FROM enrichment_job_items i
                   JOIN products p ON p.code=i.product_code
                    WHERE i.job_id=? AND i.status IN ('pending','queued')
                    ORDER BY i.product_code LIMIT 1""", (job_id,),
            ).fetchone()
            if not item:
                _refresh_job(con, job_id)
                break
            con.execute(
                """UPDATE enrichment_job_items
                   SET status='running', attempts=attempts+1, started_at=?
                   WHERE job_id=? AND product_code=?""",
                (utcnow(), job_id, item["product_code"]),
            )
        status = "failed"
        error = None
        try:
            result = fetcher(item["product_code"], item["designation"])
            status = str(result.get("status") or "review")
            save_enrichment(item["product_code"], result, db_path)
        except Exception as exc:  # one failed product must not stop the job
            error = f"{type(exc).__name__}: {exc}"[:1000]
            save_enrichment(item["product_code"], {"status": "failed", "error": error}, db_path)
        with transaction(db_path) as con:
            finished = utcnow()
            if "processed_at" in table_columns(con, "enrichment_job_items"):
                con.execute(
                    """UPDATE enrichment_job_items
                       SET status=?, error=?, finished_at=?, processed_at=?
                       WHERE job_id=? AND product_code=?""",
                    (status, error, finished, finished, job_id, item["product_code"]),
                )
            else:
                con.execute(
                    """UPDATE enrichment_job_items SET status=?, error=?, finished_at=?
                       WHERE job_id=? AND product_code=?""",
                    (status, error, finished, job_id, item["product_code"]),
                )
            _refresh_job(con, job_id)
        if pause:
            time.sleep(pause)
    return get_bulk_job(job_id, db_path) or {}


def start_bulk_job(job_id: int, db_path: str | None = None) -> threading.Thread:
    thread = threading.Thread(target=run_bulk_job, args=(job_id, db_path), daemon=True, name=f"iaquote-enrich-{job_id}")
    thread.start()
    return thread


def catalog_stats(db_path: str | None = None) -> dict[str, int]:
    with transaction(db_path) as con:
        row = con.execute(
            """SELECT COUNT(*) total,
               SUM(CASE WHEN e.status='verified' THEN 1 ELSE 0 END) verified,
               SUM(CASE WHEN e.status='review' THEN 1 ELSE 0 END) review,
               SUM(CASE WHEN e.status IN ('failed','not_found') THEN 1 ELSE 0 END) failed,
               SUM(CASE WHEN e.product_code IS NULL THEN 1 ELSE 0 END) remaining
               FROM products p LEFT JOIN enrichments e ON e.product_code=p.code"""
        ).fetchone()
    return {k: int(row[k] or 0) for k in ("total", "verified", "review", "failed", "remaining")}


def get_bulk_job(job_id: int | None = None, db_path: str | None = None) -> dict[str, Any] | None:
    with transaction(db_path) as con:
        if job_id is None:
            row = con.execute(
                """SELECT * FROM enrichment_jobs
                   ORDER BY CASE WHEN status IN ('pending','queued','running','blocked','cancelling') THEN 0 ELSE 1 END,
                            id DESC LIMIT 1"""
            ).fetchone()
        else:
            row = con.execute("SELECT * FROM enrichment_jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["items"] = [dict(r) for r in con.execute(
            """SELECT i.*,p.designation,e.source_url,e.source_title,e.confidence
               FROM enrichment_job_items i JOIN products p ON p.code=i.product_code
               LEFT JOIN enrichments e ON e.product_code=i.product_code
               WHERE i.job_id=? ORDER BY i.product_code DESC LIMIT 200""", (row["id"],)
        )]
    result["catalog"] = catalog_stats(db_path)
    result["percent"] = round((result["processed"] / result["total"] * 100), 1) if result["total"] else 0.0
    return result
