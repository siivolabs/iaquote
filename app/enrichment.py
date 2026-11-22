import json
import os
import re
import threading
import time
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import quote_plus, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .db import product_with_enrichment, transaction, utcnow

_LOCK = threading.Lock()
_LAST_REQUEST = 0.0
_WORKER_GUARD = threading.Lock()
_WORKER_THREAD = None
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml",
}


def _allowed_hosts():
    return {
        host.strip().lower()
        for host in os.getenv("TECHNICAL_ALLOWED_HOSTS", "").split(",")
        if host.strip()
    }


def _safe_source_url(url, require_product=False):
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    allowed = _allowed_hosts()
    if parsed.scheme != "https" or not host:
        raise ValueError("Seules les adresses HTTPS sont autorisées pour la source technique.")
    if not allowed:
        raise ValueError("Configurez TECHNICAL_ALLOWED_HOSTS avant d'utiliser l'enrichissement réseau.")
    if not any(host == item or host.endswith("." + item) for item in allowed):
        raise ValueError("Le domaine de la source technique n'est pas autorisé par la configuration.")
    marker = os.getenv("TECHNICAL_PRODUCT_PATH_MARKER", "/fiche/").strip()
    if require_product and marker and marker not in parsed.path:
        raise ValueError("L'adresse ne correspond pas au chemin de fiche produit configuré.")
    return url


def _get(url, require_product=False):
    global _LAST_REQUEST
    _safe_source_url(url, require_product=require_product)
    delay = float(os.getenv("TECHNICAL_REQUEST_DELAY", "2.5"))
    timeout = float(os.getenv("TECHNICAL_TIMEOUT", "25"))
    with _LOCK:
        wait = delay - (time.monotonic() - _LAST_REQUEST)
        if wait > 0:
            time.sleep(wait)
        response = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        _LAST_REQUEST = time.monotonic()
    response.raise_for_status()
    _safe_source_url(response.url, require_product=require_product)
    return response


def _clean(value):
    return re.sub(r"\s+", " ", value or "").strip()


def _score_title(designation, title, brand=""):
    a, b = designation.lower(), title.lower()
    ratio = SequenceMatcher(None, a, b).ratio()
    tokens = {x for x in re.findall(r"[a-z0-9]+", a) if len(x) > 2}
    title_tokens = set(re.findall(r"[a-z0-9]+", b))
    overlap = len(tokens & title_tokens) / max(1, len(tokens))
    score = 0.45 * ratio + 0.55 * overlap
    if brand and brand.lower() in b:
        score += 0.05
    if any(x in b for x in ("occasion", "reconditionné", "plus en vente")):
        score -= 0.25
    return max(0.0, min(1.0, score))


def _search_url(designation):
    template = os.getenv("TECHNICAL_SEARCH_URL", "").strip()
    if not template:
        raise RuntimeError("La recherche automatique n'est pas configurée. Saisissez une adresse de fiche exacte.")
    encoded = quote_plus(designation)
    try:
        url = template.format(query=encoded)
    except (KeyError, ValueError) as exc:
        raise RuntimeError("TECHNICAL_SEARCH_URL doit utiliser le marqueur {query}.") from exc
    return _safe_source_url(url)


def search_candidates(designation, brand=""):
    response = _get(_search_url(designation))
    soup = BeautifulSoup(response.text, "html.parser")
    marker = os.getenv("TECHNICAL_PRODUCT_PATH_MARKER", "/fiche/").strip()
    selector = os.getenv("TECHNICAL_LINK_SELECTOR", "a[href]").strip() or "a[href]"
    seen, candidates = set(), []
    for anchor in soup.select(selector):
        href = urljoin(response.url, anchor.get("href", ""))
        if marker and marker not in urlparse(href).path:
            continue
        try:
            href = _safe_source_url(href, require_product=True)
        except ValueError:
            continue
        if href in seen:
            continue
        seen.add(href)
        title = _clean(anchor.get("title") or anchor.get_text(" ", strip=True))
        if not title:
            continue
        candidates.append({"url": href, "title": title, "score": _score_title(designation, title, brand)})
    return sorted(candidates, key=lambda x: x["score"], reverse=True)[:8]


def _pairs_from_soup(soup):
    pairs = {}
    for row in soup.select("tr"):
        cells = row.find_all(["th", "td"], recursive=True)
        if len(cells) >= 2:
            key = _clean(cells[0].get_text(" ", strip=True))
            value = _clean(cells[-1].get_text(" ", strip=True))
            if key and value and len(key) < 120:
                pairs.setdefault(key, value)
    for term in soup.select("dt"):
        value_node = term.find_next_sibling("dd")
        if value_node:
            key = _clean(term.get_text(" ", strip=True))
            value = _clean(value_node.get_text(" ", strip=True))
            if key and value:
                pairs.setdefault(key, value)
    return pairs


def _number(text):
    match = re.search(r"(\d+(?:[.,]\d+)?)", text or "")
    return float(match.group(1).replace(",", ".")) if match else None


def normalize_specs(pairs, full_text=""):
    lower = {k.lower(): v for k, v in pairs.items()}
    specs = {"technical": pairs}

    def find(*labels):
        for label in labels:
            for key, value in lower.items():
                if label in key:
                    return value
        return None

    mapping = {
        "socket": ("support du processeur", "socket"),
        "memory_type": ("type de mémoire", "contrôleur mémoire"),
        "motherboard_format": ("format de carte mère",),
        "case_formats": ("format de carte mère",),
        "storage_interface": ("interface avec l'ordinateur", "interface"),
        "gpu_memory": ("taille mémoire vidéo", "mémoire vidéo"),
        "psu_certification": ("norme (s)", "certification"),
    }
    for key, labels in mapping.items():
        value = find(*labels)
        if value:
            specs[key] = value

    numeric = {
        "tdp_w": ("tdp",),
        "power_w": ("puissance",),
        "gpu_length_mm": ("longueur de la carte", "longueur max. carte graphique"),
        "case_max_gpu_mm": ("longueur max. carte graphique",),
        "cooler_height_mm": ("hauteur",),
        "case_max_cooler_mm": ("hauteur max. ventilateur cpu",),
        "cooler_tdp_w": ("tdp maximal", "tdp max"),
    }
    for key, labels in numeric.items():
        value = find(*labels)
        number = _number(value)
        if number is not None:
            specs[key] = number

    text = full_text.lower()
    if "socket am5" in text or "amd am5" in text:
        specs.setdefault("socket", "AMD AM5")
    if "ddr5" in text:
        specs.setdefault("memory_type", "DDR5")
    if "atx12v 3.1" in text:
        specs.setdefault("psu_standard", "ATX 3.1")
    return specs


def parse_product_page(html, url, designation, brand=""):
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.find("h1") or soup.find("title")
    title = _clean(heading.get_text(" ", strip=True) if heading else "")
    if not title:
        raise ValueError("Titre produit introuvable dans la fiche.")
    pairs = _pairs_from_soup(soup)
    text = _clean(soup.get_text(" ", strip=True))
    confidence = _score_title(designation, title, brand)
    status = "verified" if confidence >= 0.76 and pairs else "review"
    note = "Correspondance forte et caractéristiques extraites" if status == "verified" else "Contrôle humain requis : correspondance ou caractéristiques incomplètes"
    return {
        "source_url": url,
        "source_title": title,
        "status": status,
        "confidence": round(confidence, 3),
        "specs": normalize_specs(pairs, text),
        "raw_summary": text[:5000],
        "validation_note": note,
    }


def save_enrichment(code, result, db_path=None):
    with transaction(db_path) as con:
        con.execute(
            """INSERT INTO enrichments(product_code, source_url, source_title, status, confidence,
                   specs_json, raw_summary, validation_note, fetched_at)
               VALUES(?,?,?,?,?,?,?,?,?)
               ON CONFLICT(product_code) DO UPDATE SET source_url=excluded.source_url,
                   source_title=excluded.source_title, status=excluded.status,
                   confidence=excluded.confidence, specs_json=excluded.specs_json,
                   raw_summary=excluded.raw_summary, validation_note=excluded.validation_note,
                   fetched_at=excluded.fetched_at""",
            (
                code,
                result.get("source_url"),
                result.get("source_title"),
                result["status"],
                result.get("confidence", 0),
                json.dumps(result.get("specs", {}), ensure_ascii=False),
                result.get("raw_summary", ""),
                result.get("validation_note", ""),
                utcnow(),
            ),
        )


def enrich_product(code, source_url=None, force=False, db_path=None):
    with transaction(db_path) as con:
        product = product_with_enrichment(con, code)
    if not product:
        raise KeyError(f"Référence inconnue : {code}")
    if product.get("enrichment_status") in {"verified", "review"} and not force and not source_url:
        return product

    if source_url:
        url = _safe_source_url(source_url, require_product=True)
    else:
        candidates = search_candidates(product["designation"], product.get("brand", ""))
        if not candidates:
            raise RuntimeError("Aucune fiche technique trouvée automatiquement. Saisissez l'adresse exacte de la fiche.")
        url = candidates[0]["url"]
    response = _get(url, require_product=True)
    result = parse_product_page(response.text, response.url, product["designation"], product.get("brand", ""))
    save_enrichment(code, result, db_path)
    with transaction(db_path) as con:
        return product_with_enrichment(con, code)


def technical_source_ready():
    """Indique si la recherche automatique peut réellement fonctionner."""
    return bool(os.getenv("TECHNICAL_SEARCH_URL", "").strip() and _allowed_hosts())


def _job_dict(row):
    if not row:
        return None
    item = dict(row)
    item["force"] = bool(item.get("force"))
    total = int(item.get("total") or 0)
    processed = int(item.get("processed") or 0)
    item["remaining"] = max(0, total - processed)
    item["progress_percent"] = round((processed / total * 100) if total else 100, 1)
    return item


def get_bulk_job(job_id=None, db_path=None):
    with transaction(db_path) as con:
        if job_id is None:
            row = con.execute("SELECT * FROM enrichment_jobs ORDER BY id DESC LIMIT 1").fetchone()
        else:
            row = con.execute("SELECT * FROM enrichment_jobs WHERE id=?", (job_id,)).fetchone()
    return _job_dict(row)


def _eligible_codes(con, codes=None, force=False):
    params = []
    where = ["1=1"]
    if codes is not None:
        clean_codes = sorted({str(code).strip() for code in codes if str(code).strip()})
        if not clean_codes:
            return []
        placeholders = ",".join("?" for _ in clean_codes)
        where.append(f"p.code IN ({placeholders})")
        params.extend(clean_codes)
    if not force:
        where.append("(e.product_code IS NULL OR e.status='review')")
    where.append(
        """NOT EXISTS (
            SELECT 1 FROM enrichment_job_items ji
            JOIN enrichment_jobs j ON j.id=ji.job_id
            WHERE ji.product_code=p.code
              AND ji.status IN ('queued','running')
              AND j.status IN ('queued','running','blocked','cancelling')
        )"""
    )
    rows = con.execute(
        f"""SELECT p.code
            FROM products p
            LEFT JOIN enrichments e ON e.product_code=p.code
            WHERE {' AND '.join(where)}
            ORDER BY p.category, p.designation""",
        params,
    ).fetchall()
    return [row[0] for row in rows]


def queue_bulk_enrichment(codes=None, force=False, trigger="manual", db_path=None, launch=True):
    """Crée une tâche persistante et la traite en arrière-plan, un produit à la fois."""
    ready = technical_source_ready()
    created_at = utcnow()
    with transaction(db_path) as con:
        selected = _eligible_codes(con, codes, force)
        status = "queued" if ready and selected else "blocked" if selected else "completed"
        last_error = None
        if status == "blocked":
            last_error = "La recherche technique automatique n'est pas configurée."
        cur = con.execute(
            """INSERT INTO enrichment_jobs(
                   created_at, finished_at, status, trigger, force, total, processed, last_error
               ) VALUES(?,?,?,?,?,?,?,?)""",
            (
                created_at,
                created_at if status == "completed" else None,
                status,
                trigger,
                1 if force else 0,
                len(selected),
                0,
                last_error,
            ),
        )
        job_id = cur.lastrowid
        con.executemany(
            "INSERT INTO enrichment_job_items(job_id, product_code, status) VALUES(?,?,'queued')",
            [(job_id, code) for code in selected],
        )
    if launch and status == "queued":
        _ensure_worker(db_path)
    return get_bulk_job(job_id, db_path)


def _refresh_job_counts(con, job_id):
    counts = {
        row["status"]: row["count"]
        for row in con.execute(
            "SELECT status, COUNT(*) AS count FROM enrichment_job_items WHERE job_id=? GROUP BY status",
            (job_id,),
        ).fetchall()
    }
    verified = counts.get("verified", 0)
    review = counts.get("review", 0)
    failed = counts.get("failed", 0)
    cancelled = counts.get("cancelled", 0)
    processed = verified + review + failed + cancelled
    con.execute(
        """UPDATE enrichment_jobs
           SET processed=?, verified=?, review=?, failed=?, cancelled=?
           WHERE id=?""",
        (processed, verified, review, failed, cancelled, job_id),
    )
    return counts


def _finish_cancelled_job(con, job_id):
    con.execute(
        """UPDATE enrichment_job_items
           SET status='cancelled', processed_at=?
           WHERE job_id=? AND status IN ('queued','running')""",
        (utcnow(), job_id),
    )
    _refresh_job_counts(con, job_id)
    con.execute(
        "UPDATE enrichment_jobs SET status='cancelled', finished_at=? WHERE id=?",
        (utcnow(), job_id),
    )


def _process_job(job_id, db_path):
    with transaction(db_path) as con:
        con.execute(
            "UPDATE enrichment_jobs SET status='running', started_at=COALESCE(started_at, ?) WHERE id=?",
            (utcnow(), job_id),
        )
    while True:
        with transaction(db_path) as con:
            job = con.execute("SELECT status, force FROM enrichment_jobs WHERE id=?", (job_id,)).fetchone()
            if not job or job["status"] in {"cancelling", "cancelled"}:
                if job:
                    _finish_cancelled_job(con, job_id)
                return
            item = con.execute(
                """SELECT product_code FROM enrichment_job_items
                   WHERE job_id=? AND status='queued' ORDER BY rowid LIMIT 1""",
                (job_id,),
            ).fetchone()
            if not item:
                counts = _refresh_job_counts(con, job_id)
                final_status = "completed_with_errors" if counts.get("failed", 0) else "completed"
                con.execute(
                    "UPDATE enrichment_jobs SET status=?, finished_at=? WHERE id=?",
                    (final_status, utcnow(), job_id),
                )
                return
            code = item["product_code"]
            con.execute(
                "UPDATE enrichment_job_items SET status='running' WHERE job_id=? AND product_code=?",
                (job_id, code),
            )
            force = bool(job["force"])

        item_status = "failed"
        error = None
        try:
            enriched = enrich_product(code, force=force, db_path=db_path)
            item_status = "verified" if enriched.get("enrichment_status") == "verified" else "review"
        except Exception as exc:  # L'échec d'un article ne doit jamais arrêter les 299 suivants.
            error = str(exc)[:1000]

        with transaction(db_path) as con:
            con.execute(
                """UPDATE enrichment_job_items
                   SET status=?, error=?, processed_at=?
                   WHERE job_id=? AND product_code=?""",
                (item_status, error, utcnow(), job_id, code),
            )
            if error:
                con.execute("UPDATE enrichment_jobs SET last_error=? WHERE id=?", (error, job_id))
            _refresh_job_counts(con, job_id)


def _worker_loop(db_path):
    global _WORKER_THREAD
    try:
        while True:
            with transaction(db_path) as con:
                row = con.execute(
                    "SELECT id FROM enrichment_jobs WHERE status='queued' ORDER BY id LIMIT 1"
                ).fetchone()
            if not row:
                return
            _process_job(row["id"], db_path)
    finally:
        with _WORKER_GUARD:
            _WORKER_THREAD = None
        # Ferme la petite fenêtre de concurrence où un import peut ajouter une
        # tâche juste après notre dernière lecture de la file.
        with transaction(db_path) as con:
            still_queued = con.execute(
                "SELECT 1 FROM enrichment_jobs WHERE status='queued' LIMIT 1"
            ).fetchone()
        if still_queued:
            _ensure_worker(db_path)


def _ensure_worker(db_path=None):
    global _WORKER_THREAD
    with _WORKER_GUARD:
        if _WORKER_THREAD and _WORKER_THREAD.is_alive():
            return
        _WORKER_THREAD = threading.Thread(
            target=_worker_loop,
            args=(db_path,),
            name="technical-enrichment",
            daemon=True,
        )
        _WORKER_THREAD.start()


def resume_bulk_enrichment(db_path=None):
    """Reprend les tâches interrompues après un redémarrage du conteneur."""
    ready = technical_source_ready()
    with transaction(db_path) as con:
        cancelling = [
            row[0]
            for row in con.execute(
                "SELECT id FROM enrichment_jobs WHERE status='cancelling'"
            ).fetchall()
        ]
        for job_id in cancelling:
            _finish_cancelled_job(con, job_id)
        con.execute(
            "UPDATE enrichment_job_items SET status='queued' WHERE status='running'"
        )
        con.execute(
            "UPDATE enrichment_jobs SET status='queued' WHERE status='running'"
        )
        if ready:
            con.execute(
                "UPDATE enrichment_jobs SET status='queued', last_error=NULL WHERE status='blocked'"
            )
        queued = con.execute("SELECT COUNT(*) FROM enrichment_jobs WHERE status='queued'").fetchone()[0]
    if queued:
        _ensure_worker(db_path)
    return queued


def cancel_bulk_enrichment(job_id, db_path=None):
    with transaction(db_path) as con:
        job = con.execute("SELECT status FROM enrichment_jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            raise KeyError(f"Tâche d'enrichissement introuvable : {job_id}")
        if job["status"] in {"queued", "blocked"}:
            _finish_cancelled_job(con, job_id)
        elif job["status"] == "running":
            con.execute("UPDATE enrichment_jobs SET status='cancelling' WHERE id=?", (job_id,))
    return get_bulk_job(job_id, db_path)


def seed_preverified(path=None, db_path=None):
    path = path or os.getenv("PRESEED_PATH", "")
    if not path or not Path(path).is_file():
        return 0
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    count = 0
    with transaction(db_path) as con:
        for item in data:
            exists = con.execute("SELECT 1 FROM products WHERE code=?", (item["code"],)).fetchone()
            if not exists:
                continue
            current = con.execute("SELECT 1 FROM enrichments WHERE product_code=?", (item["code"],)).fetchone()
            if current:
                continue
            con.execute(
                """INSERT INTO enrichments(product_code, source_url, source_title, status, confidence,
                   specs_json, raw_summary, validation_note, fetched_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    item["code"],
                    item.get("source_url", ""),
                    item.get("source_title", "Fiche technique de démonstration"),
                    item.get("status", "verified"),
                    item.get("confidence", 1.0),
                    json.dumps(item.get("specs", {}), ensure_ascii=False),
                    item.get("raw_summary", "Données techniques de démonstration"),
                    item.get("validation_note", "Données techniques de démonstration"),
                    item.get("fetched_at", "2026-01-01T00:00:00+00:00"),
                ),
            )
            count += 1
    return count
