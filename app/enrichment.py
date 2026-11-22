import json
import os
import re
import threading
import time
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .db import product_with_enrichment, transaction, utcnow

CORRECTIVE_VERSION = "0.3.1"
_LOCK = threading.Lock()
_LAST_REQUEST = 0.0
_WORKER_GUARD = threading.Lock()
_WORKER_THREAD = None
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/json",
}
_DEFAULT_DISCOVERY_TEMPLATE = (
    "https://html.duckduckgo.com/html/?q={query}+site%3A{domain}"
)


def _env_enabled(name, default=False):
    value = os.getenv(name)
    if value is None:
        return bool(default)
    return value.strip().lower() not in {"0", "false", "no", "off", "disabled"}


def _csv_env(name, default=""):
    return {
        value.strip().lower()
        for value in os.getenv(name, default).replace("\n", ",").split(",")
        if value.strip()
    }


def _allowed_hosts():
    return _csv_env("TECHNICAL_ALLOWED_HOSTS")


def _search_hosts():
    return _csv_env(
        "TECHNICAL_SEARCH_ALLOWED_HOSTS",
        "html.duckduckgo.com,duckduckgo.com",
    ) | _allowed_hosts()


def _host_is_allowed(host, allowed):
    host = (host or "").lower()
    return any(host == item or host.endswith("." + item) for item in allowed)


def _safe_source_url(url, require_product=False):
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    allowed = _allowed_hosts()
    if parsed.scheme != "https" or not host:
        raise ValueError("Seules les adresses HTTPS sont autorisées pour la source technique.")
    if not allowed:
        raise ValueError(
            "Configurez TECHNICAL_ALLOWED_HOSTS avant d'utiliser l'enrichissement réseau."
        )
    if not _host_is_allowed(host, allowed):
        raise ValueError("Le domaine de la source technique n'est pas autorisé par la configuration.")
    marker = os.getenv("TECHNICAL_PRODUCT_PATH_MARKER", "/fiche/").strip()
    if require_product and marker and marker not in parsed.path:
        raise ValueError("L'adresse ne correspond pas au chemin de fiche produit configuré.")
    return url


def _safe_search_url(url):
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host:
        raise ValueError("Seules les adresses HTTPS sont autorisées pour la recherche technique.")
    if not _host_is_allowed(host, _search_hosts()):
        raise ValueError("Le domaine du moteur de recherche n'est pas autorisé.")
    return url


def _throttled_get(url, validator, delay_name, timeout_name):
    global _LAST_REQUEST
    validator(url)
    delay = float(os.getenv(delay_name, "2.5"))
    timeout = float(os.getenv(timeout_name, "25"))
    with _LOCK:
        wait = delay - (time.monotonic() - _LAST_REQUEST)
        if wait > 0:
            time.sleep(wait)
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=timeout,
            allow_redirects=True,
        )
        _LAST_REQUEST = time.monotonic()
    response.raise_for_status()
    validator(response.url)
    return response


def _get(url, require_product=False):
    def validate(value):
        return _safe_source_url(value, require_product=require_product)

    return _throttled_get(
        url,
        validate,
        "TECHNICAL_REQUEST_DELAY",
        "TECHNICAL_TIMEOUT",
    )


def _get_search(url):
    return _throttled_get(
        url,
        _safe_search_url,
        "TECHNICAL_SEARCH_DELAY",
        "TECHNICAL_SEARCH_TIMEOUT",
    )


def _clean(value):
    return re.sub(r"\s+", " ", value or "").strip()


def _score_title(designation, title, brand=""):
    a, b = designation.lower(), title.lower()
    ratio = SequenceMatcher(None, a, b).ratio()
    tokens = {value for value in re.findall(r"[a-z0-9]+", a) if len(value) > 2}
    title_tokens = set(re.findall(r"[a-z0-9]+", b))
    overlap = len(tokens & title_tokens) / max(1, len(tokens))
    score = 0.45 * ratio + 0.55 * overlap
    if brand and brand.lower() in b:
        score += 0.05
    if any(value in b for value in ("occasion", "reconditionné", "plus en vente")):
        score -= 0.25
    return max(0.0, min(1.0, score))


def _primary_domain():
    hosts = sorted(_allowed_hosts())
    return hosts[0] if hosts else ""


def _format_search_url(template, designation):
    encoded = quote_plus(designation)
    domain = quote_plus(_primary_domain())
    try:
        url = template.format(
            query=encoded,
            domain=domain,
            raw_query=designation,
        )
    except (KeyError, ValueError) as exc:
        raise RuntimeError(
            "L'adresse de recherche doit utiliser {query} et peut utiliser {domain}."
        ) from exc
    return _safe_search_url(url)


def _search_templates():
    templates = []
    for name in ("TECHNICAL_SEARCH_API_URL", "TECHNICAL_SEARCH_URL"):
        value = os.getenv(name, "").strip()
        if value:
            templates.append(value)
    templates.extend(
        value.strip()
        for value in os.getenv("TECHNICAL_SEARCH_FALLBACK_URLS", "").splitlines()
        if value.strip()
    )
    if _env_enabled("TECHNICAL_DEFAULT_DISCOVERY", True):
        templates.append(_DEFAULT_DISCOVERY_TEMPLATE)
    return list(dict.fromkeys(templates))


def technical_source_ready():
    """Retourne vrai si une source et au moins une méthode de découverte sont utilisables."""
    return bool(_allowed_hosts() and _search_templates())


def _decode_result_url(href, base_url):
    absolute = urljoin(base_url, href)
    parsed = urlparse(absolute)
    values = parse_qs(parsed.query)
    for key in ("uddg", "url", "u", "target", "dest", "destination"):
        if values.get(key):
            candidate = unquote(values[key][0])
            if candidate.startswith("http://") or candidate.startswith("https://"):
                return candidate
    return absolute


def _add_candidate(candidates, seen, url, title, designation, brand):
    if not url or not title:
        return
    try:
        url = _safe_source_url(url, require_product=True)
    except ValueError:
        return
    if url in seen:
        return
    seen.add(url)
    title = _clean(title)
    if not title:
        return
    candidates.append(
        {
            "url": url,
            "title": title,
            "score": _score_title(designation, title, brand),
        }
    )


def _json_result_rows(payload):
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                yield item
        return
    if not isinstance(payload, dict):
        return
    for key in ("results", "items", "webPages", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    yield item
        elif isinstance(value, dict):
            nested = value.get("value") or value.get("results") or value.get("items")
            if isinstance(nested, list):
                for item in nested:
                    if isinstance(item, dict):
                        yield item


def _candidates_from_json(payload, designation, brand, seen, candidates):
    for item in _json_result_rows(payload):
        url = item.get("url") or item.get("link") or item.get("href")
        title = item.get("title") or item.get("name") or item.get("text")
        _add_candidate(candidates, seen, url, title, designation, brand)


def _candidates_from_html(response, designation, brand, seen, candidates):
    soup = BeautifulSoup(response.text, "html.parser")
    selector = os.getenv("TECHNICAL_LINK_SELECTOR", "a[href]").strip() or "a[href]"
    for anchor in soup.select(selector):
        href = _decode_result_url(anchor.get("href", ""), response.url)
        title = anchor.get("title") or anchor.get_text(" ", strip=True)
        _add_candidate(candidates, seen, href, title, designation, brand)


def search_candidates(designation, brand=""):
    """Découvre des fiches via JSON ou HTML, avec repli indépendant du moteur du site."""
    if not technical_source_ready():
        raise RuntimeError(
            "La recherche automatique n'est pas configurée. Configurez au moins les domaines sources autorisés."
        )
    seen, candidates, errors = set(), [], []
    for template in _search_templates():
        try:
            response = _get_search(_format_search_url(template, designation))
            content_type = response.headers.get("content-type", "").lower()
            if "json" in content_type or response.text.lstrip().startswith(("{", "[")):
                _candidates_from_json(response.json(), designation, brand, seen, candidates)
            else:
                _candidates_from_html(response, designation, brand, seen, candidates)
        except Exception as exc:
            errors.append(str(exc))
            continue
        stop_score = float(os.getenv("TECHNICAL_SEARCH_STOP_SCORE", "0.72"))
        if candidates and max(item["score"] for item in candidates) >= stop_score:
            break
    if not candidates and errors:
        raise RuntimeError(
            "Aucune fiche technique trouvée automatiquement. "
            "Les moteurs interrogés n'ont fourni aucun résultat exploitable."
        )
    return sorted(candidates, key=lambda item: item["score"], reverse=True)[:8]


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
    lower = {key.lower(): value for key, value in pairs.items()}
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
        number = _number(find(*labels))
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
    note = (
        "Correspondance forte et caractéristiques extraites"
        if status == "verified"
        else "Contrôle humain requis : correspondance ou caractéristiques incomplètes"
    )
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
    """Enregistre sans supposer l'ordre des colonnes de la base existante."""
    values = {
        "product_code": code,
        "source_url": result["source_url"],
        "source_title": result.get("source_title"),
        "status": result.get("status", "review"),
        "confidence": result.get("confidence", 0),
        "specs_json": json.dumps(result.get("specs", {}), ensure_ascii=False),
        "raw_summary": result.get("raw_summary"),
        "validation_note": result.get("validation_note"),
        "checked_at": utcnow(),
        "updated_at": utcnow(),
        "verified_at": utcnow(),
    }
    with transaction(db_path) as con:
        columns = {
            row["name"] if hasattr(row, "keys") else row[1]
            for row in con.execute("PRAGMA table_info(enrichments)").fetchall()
        }
        selected = [name for name in values if name in columns]
        if "product_code" not in selected:
            raise RuntimeError("Schéma de la table enrichments incompatible.")
        placeholders = ",".join("?" for _ in selected)
        updates = ",".join(
            f"{name}=excluded.{name}" for name in selected if name != "product_code"
        )
        sql = (
            f"INSERT INTO enrichments({','.join(selected)}) VALUES({placeholders}) "
            f"ON CONFLICT(product_code) DO UPDATE SET {updates}"
        )
        con.execute(sql, [values[name] for name in selected])


def enrich_product(code, source_url=None, force=False, db_path=None):
    with transaction(db_path) as con:
        product = product_with_enrichment(con, code)
    if not product:
        raise KeyError(f"Produit introuvable : {code}")
    if (
        product.get("enrichment_status") in {"verified", "review"}
        and not force
        and not source_url
    ):
        return product

    designation = product.get("designation") or product.get("name") or code
    brand = product.get("brand") or product.get("manufacturer") or ""
    if source_url:
        urls = [{"url": _safe_source_url(source_url, require_product=True), "score": 1.0}]
    else:
        urls = search_candidates(designation, brand)
    if not urls:
        raise RuntimeError(
            "Aucune fiche technique trouvée automatiquement. Saisissez l'adresse exacte de la fiche."
        )

    best, errors = None, []
    for candidate in urls[:4]:
        try:
            response = _get(candidate["url"], require_product=True)
            result = parse_product_page(response.text, response.url, designation, brand)
            result["confidence"] = round(
                max(result.get("confidence", 0), candidate.get("score", 0)), 3
            )
            if result["confidence"] >= 0.76 and result["specs"].get("technical"):
                result["status"] = "verified"
                result["validation_note"] = "Correspondance forte et caractéristiques extraites"
            if best is None or result["confidence"] > best["confidence"]:
                best = result
            if result["status"] == "verified":
                break
        except Exception as exc:
            errors.append(str(exc))
    if best is None:
        detail = errors[-1] if errors else "aucun résultat exploitable"
        raise RuntimeError(f"Échec de lecture des fiches candidates : {detail}")
    save_enrichment(code, best, db_path)
    with transaction(db_path) as con:
        return product_with_enrichment(con, code)


def _job_dict(row):
    if not row:
        return None
    item = dict(row)
    item["force"] = bool(item.get("force"))
    total = int(item.get("total") or 0)
    processed = int(item.get("processed") or 0)
    item["remaining"] = max(0, total - processed)
    item["progress_percent"] = round((processed / total * 100) if total else 0, 1)
    return item


def _catalog_job(con):
    total = con.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    rows = con.execute(
        "SELECT status, COUNT(*) AS count FROM enrichments GROUP BY status"
    ).fetchall()
    counts = {row["status"]: row["count"] for row in rows}
    verified = int(counts.get("verified", 0))
    review = int(counts.get("review", 0))
    failed_row = con.execute(
        """SELECT COUNT(DISTINCT ji.product_code)
           FROM enrichment_job_items ji
           WHERE ji.status='failed'
             AND NOT EXISTS (
                 SELECT 1 FROM enrichments e WHERE e.product_code=ji.product_code
             )"""
    ).fetchone()
    failed = int(failed_row[0] or 0)
    active = con.execute(
        """SELECT * FROM enrichment_jobs
           WHERE status IN ('queued','running','blocked','cancelling') AND total>0
           ORDER BY CASE status WHEN 'running' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END, id
           LIMIT 1"""
    ).fetchone()
    if active:
        return _job_dict(active)
    meaningful = con.execute(
        "SELECT * FROM enrichment_jobs WHERE total>0 ORDER BY id DESC LIMIT 1"
    ).fetchone()
    last = dict(meaningful) if meaningful else {}
    successful = verified + review
    if total == 0:
        status = "completed"
    elif successful >= total:
        status = "completed"
    elif failed:
        status = "completed_with_errors"
    elif last.get("status") in {"blocked", "cancelled"}:
        status = last["status"]
    else:
        status = "queued"
    return {
        "id": last.get("id"),
        "created_at": last.get("created_at"),
        "started_at": last.get("started_at"),
        "finished_at": last.get("finished_at"),
        "status": status,
        "trigger": "catalog",
        "force": False,
        "total": total,
        "processed": successful,
        "attempted": successful + failed,
        "verified": verified,
        "review": review,
        "failed": failed,
        "cancelled": 0,
        "remaining": max(0, total - successful),
        "last_error": last.get("last_error"),
        "progress_percent": round((successful / total * 100) if total else 0, 1),
        "catalog_summary": True,
    }


def get_bulk_job(job_id=None, db_path=None):
    with transaction(db_path) as con:
        if job_id is None:
            return _catalog_job(con)
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


def _matching_active_job(con, codes=None):
    if codes is None:
        return con.execute(
            """SELECT * FROM enrichment_jobs
               WHERE status IN ('queued','running','blocked','cancelling') AND total>0
               ORDER BY id LIMIT 1"""
        ).fetchone()
    clean = sorted({str(code).strip() for code in codes if str(code).strip()})
    if not clean:
        return None
    placeholders = ",".join("?" for _ in clean)
    return con.execute(
        f"""SELECT j.* FROM enrichment_jobs j
            JOIN enrichment_job_items ji ON ji.job_id=j.id
            WHERE j.status IN ('queued','running','blocked','cancelling')
              AND ji.product_code IN ({placeholders})
            ORDER BY j.id LIMIT 1""",
        clean,
    ).fetchone()


def queue_bulk_enrichment(codes=None, force=False, trigger="manual", db_path=None, launch=True):
    """Crée une tâche non vide ou reprend la tâche persistante qui possède déjà les produits."""
    ready = technical_source_ready()
    created_at = utcnow()
    job_id = None
    status = None
    with transaction(db_path) as con:
        active = _matching_active_job(con, codes)
        if active:
            job_id = active["id"]
            status = active["status"]
            if status == "blocked" and ready:
                con.execute(
                    "UPDATE enrichment_jobs SET status='queued', last_error=NULL WHERE id=?",
                    (job_id,),
                )
                status = "queued"
        else:
            selected = _eligible_codes(con, codes, force)
            if selected:
                status = "queued" if ready else "blocked"
                last_error = None if ready else "La recherche technique automatique n'est pas configurée."
                cur = con.execute(
                    """INSERT INTO enrichment_jobs(
                           created_at, finished_at, status, trigger, force, total, processed, last_error
                       ) VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        created_at,
                        None,
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
    if job_id is None:
        return get_bulk_job(None, db_path)
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
        claimed = con.execute(
            """UPDATE enrichment_jobs
               SET status='running', started_at=COALESCE(started_at, ?)
               WHERE id=? AND status='queued'""",
            (utcnow(), job_id),
        )
        if claimed.rowcount != 1:
            return
    while True:
        with transaction(db_path) as con:
            job = con.execute(
                "SELECT status, force FROM enrichment_jobs WHERE id=?", (job_id,)
            ).fetchone()
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
            updated = con.execute(
                """UPDATE enrichment_job_items SET status='running'
                   WHERE job_id=? AND product_code=? AND status='queued'""",
                (job_id, code),
            )
            if updated.rowcount != 1:
                continue
            force = bool(job["force"])

        item_status = "failed"
        error = None
        try:
            enriched = enrich_product(code, force=force, db_path=db_path)
            item_status = (
                "verified" if enriched.get("enrichment_status") == "verified" else "review"
            )
        except Exception as exc:
            error = str(exc)[:1000]

        with transaction(db_path) as con:
            con.execute(
                """UPDATE enrichment_job_items
                   SET status=?, error=?, processed_at=?
                   WHERE job_id=? AND product_code=?""",
                (item_status, error, utcnow(), job_id, code),
            )
            if error:
                con.execute(
                    "UPDATE enrichment_jobs SET last_error=? WHERE id=?",
                    (error, job_id),
                )
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


def _ensure_worker(db_path=None):
    global _WORKER_THREAD
    with _WORKER_GUARD:
        if _WORKER_THREAD and _WORKER_THREAD.is_alive():
            return False
        _WORKER_THREAD = threading.Thread(
            target=_worker_loop,
            args=(db_path,),
            name="iaquote-enrichment",
            daemon=True,
        )
        _WORKER_THREAD.start()
        return True


def resume_bulk_enrichment(db_path=None):
    """Répare les éléments interrompus et reprend les tâches sans créer de doublon vide."""
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
            """UPDATE enrichment_job_items SET status='queued', error=NULL, processed_at=NULL
               WHERE status='running' AND job_id IN (
                   SELECT id FROM enrichment_jobs WHERE status='running'
               )"""
        )
        con.execute("UPDATE enrichment_jobs SET status='queued' WHERE status='running'")
        if ready:
            con.execute(
                "UPDATE enrichment_jobs SET status='queued', last_error=NULL WHERE status='blocked'"
            )
        queued = con.execute(
            "SELECT COUNT(*) FROM enrichment_jobs WHERE status='queued' AND total>0"
        ).fetchone()[0]
    if queued:
        _ensure_worker(db_path)
    return queued


def cancel_bulk_enrichment(job_id, db_path=None):
    with transaction(db_path) as con:
        job = con.execute(
            "SELECT status FROM enrichment_jobs WHERE id=?", (job_id,)
        ).fetchone()
        if not job:
            raise KeyError(f"Tâche d'enrichissement introuvable : {job_id}")
        if job["status"] in {"queued", "blocked"}:
            _finish_cancelled_job(con, job_id)
        elif job["status"] == "running":
            con.execute(
                "UPDATE enrichment_jobs SET status='cancelling' WHERE id=?", (job_id,)
            )
    return get_bulk_job(job_id, db_path)


def seed_preverified(path=None, db_path=None):
    path = path or os.getenv("PRESEED_PATH", "")
    if not path or not Path(path).is_file():
        return 0
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    count = 0
    for item in data:
        with transaction(db_path) as con:
            exists = con.execute(
                "SELECT 1 FROM products WHERE code=?", (item["code"],)
            ).fetchone()
            current = con.execute(
                "SELECT 1 FROM enrichments WHERE product_code=?", (item["code"],)
            ).fetchone()
        if not exists or current:
            continue
        save_enrichment(item["code"], item, db_path)
        count += 1
    return count
