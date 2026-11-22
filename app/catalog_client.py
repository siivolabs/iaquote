from __future__ import annotations

import html
import json
import re
import time
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from urllib.parse import quote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class CatalogueError(RuntimeError):
    pass


class ProductNotFound(CatalogueError):
    pass


class ProductMismatch(CatalogueError):
    pass


@dataclass(frozen=True)
class ProductSheet:
    url: str
    title: str
    match_score: float
    specifications: dict[str, str]


def normalise(value: str) -> str:
    value = unicodedata.normalize("NFKD", html.unescape(value or ""))
    value = "".join(c for c in value if not unicodedata.combining(c)).lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def similarity(expected: str, actual: str) -> float:
    left, right = normalise(expected), normalise(actual)
    if not left or not right:
        return 0.0
    lt, rt = set(left.split()), set(right.split())
    important = {t for t in lt if len(t) > 2}
    coverage = len(important & rt) / max(1, len(important))
    sequence = SequenceMatcher(None, left, right).ratio()
    model_tokens = {t for t in important if any(c.isdigit() for c in t)}
    model = len(model_tokens & rt) / len(model_tokens) if model_tokens else coverage
    return round(0.45 * coverage + 0.35 * sequence + 0.20 * model, 4)


def extract_title(soup: BeautifulSoup) -> str:
    candidates = [
        soup.select_one("h1"),
        soup.select_one('[itemprop="name"]'),
        soup.select_one('meta[property="og:title"]'),
        soup.title,
    ]
    for node in candidates:
        if node:
            value = node.get("content") if node.name == "meta" else node.get_text(" ", strip=True)
            if value:
                return re.sub(r"\s+", " ", value).strip()
    return ""


def extract_specifications(soup: BeautifulSoup) -> dict[str, str]:
    specs: dict[str, str] = {}
    for row in soup.select("table tr"):
        cells = row.find_all(["th", "td"], recursive=True)
        if len(cells) >= 2:
            key = cells[0].get_text(" ", strip=True).rstrip(":")
            value = cells[-1].get_text(" ", strip=True)
            if key and value and normalise(key) != normalise(value):
                specs.setdefault(key, value)
    for term in soup.select("dt"):
        value_node = term.find_next_sibling("dd")
        if value_node:
            key = term.get_text(" ", strip=True).rstrip(":")
            value = value_node.get_text(" ", strip=True)
            if key and value:
                specs.setdefault(key, value)
    for item in soup.select('[class*="spec"], [class*="carac"], [data-specification]'):
        label = item.select_one(".label, .name, [class*='label'], [class*='name']")
        value = item.select_one(".value, [class*='value']")
        if label and value:
            key, val = label.get_text(" ", strip=True).rstrip(":"), value.get_text(" ", strip=True)
            if key and val:
                specs.setdefault(key, val)
    return dict(list(specs.items())[:300])


class CatalogueClient:
    def __init__(self, base_url: str, *, timeout: float = 25, threshold: float = 0.58, delay: float = 0):
        if not base_url:
            raise CatalogueError("CATALOG_SEARCH_BASE_URL n'est pas configuré.")
        self.base_url = base_url.strip()
        self.timeout = timeout
        self.threshold = threshold
        self.delay = max(0, delay)
        self.session = requests.Session()
        retries = Retry(total=2, backoff_factor=0.6, status_forcelist=(429, 500, 502, 503, 504), allowed_methods=("GET",))
        self.session.mount("https://", HTTPAdapter(max_retries=retries))
        self.session.mount("http://", HTTPAdapter(max_retries=retries))
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.7",
            }
        )

    def search_url(self, code: str) -> str:
        encoded = quote(str(code).strip(), safe="")
        return self.base_url.replace("{code}", encoded) if "{code}" in self.base_url else self.base_url.rstrip("/") + "/" + encoded

    @staticmethod
    def _is_sheet(url: str) -> bool:
        return "/fiche/" in urlparse(url).path.lower()

    def _resolve_sheet(self, response: requests.Response, soup: BeautifulSoup, expected: str) -> str:
        if self._is_sheet(response.url):
            return response.url
        candidates = []
        for selector in ('link[rel="canonical"]', 'meta[property="og:url"]', 'a[href*="/fiche/"]'):
            for node in soup.select(selector):
                href = node.get("href") or node.get("content") or ""
                if href:
                    candidates.append(urljoin(response.url, href))
        unique = list(dict.fromkeys(u for u in candidates if self._is_sheet(u)))
        if not unique:
            raise ProductNotFound("La recherche par code n'a retourné aucune fiche produit.")
        if len(unique) == 1:
            return unique[0]
        ranked = []
        for link in unique[:12]:
            anchor = soup.select_one(f'a[href="{urlparse(link).path}"]') or soup.select_one(f'a[href="{link}"]')
            label = anchor.get_text(" ", strip=True) if anchor else ""
            ranked.append((similarity(expected, label), link))
        ranked.sort(reverse=True)
        if ranked[0][0] < self.threshold:
            raise ProductNotFound("Plusieurs fiches ont été trouvées, mais aucune correspondance fiable.")
        return ranked[0][1]

    def fetch(self, code: str, designation: str) -> ProductSheet:
        search = self.search_url(code)
        response = self.session.get(search, timeout=self.timeout, allow_redirects=True)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        sheet_url = self._resolve_sheet(response, soup, designation)
        if sheet_url != response.url:
            if self.delay:
                time.sleep(self.delay)
            response = self.session.get(sheet_url, timeout=self.timeout, allow_redirects=True)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
        title = extract_title(soup)
        score = similarity(designation, title)
        if score < self.threshold:
            raise ProductMismatch(f"Correspondance insuffisante ({score:.0%}) : {title or 'titre absent'}")
        specifications = extract_specifications(soup)
        if not specifications:
            raise CatalogueError("La fiche correspond, mais aucune caractéristique structurée n'a été extraite.")
        return ProductSheet(response.url, title, score, specifications)
