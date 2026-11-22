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
