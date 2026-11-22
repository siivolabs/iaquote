import pytest
from bs4 import BeautifulSoup
from app.catalog_client import CatalogueClient, CatalogueError, ProductMismatch, extract_specifications, extract_title, normalise, similarity


def test_normalise_accents_and_symbols():
    assert normalise("Mémoire 32 Go – CL19") == "memoire 32 go cl19"


def test_similarity_is_high_for_matching_product():
    assert similarity("G.Skill Aegis 32 Go DDR4 2666 MHz CL19", "G.Skill Aegis 32 Go (2 x 16 Go) DDR4 2666 MHz CL19 - Mémoire PC") > .72


def test_similarity_is_low_for_unrelated_product():
    assert similarity("Câble SATA 50 cm", "Processeur 16 cœurs 5.2 GHz") < .35


def test_extract_title_prefers_h1():
    soup = BeautifulSoup("<title>Fallback</title><h1>Produit précis</h1>", "html.parser")
    assert extract_title(soup) == "Produit précis"


def test_extract_specs_from_table_and_definition_list():
    soup = BeautifulSoup("<table><tr><th>Capacité</th><td>32 Go</td></tr></table><dl><dt>Couleur</dt><dd>Noir</dd></dl>", "html.parser")
    assert extract_specifications(soup) == {"Capacité": "32 Go", "Couleur": "Noir"}


def test_search_url_supports_placeholder_and_encoding():
    c = CatalogueClient("https://catalog.test/recherche/{code}")
    assert c.search_url("AB 12/3") == "https://catalog.test/recherche/AB%2012%2F3"


def test_search_url_supports_trailing_slash():
    c = CatalogueClient("https://catalog.test/recherche/")
    assert c.search_url("A1") == "https://catalog.test/recherche/A1"


class FakeResponse:
    def __init__(self, url, body, status=200):
        self.url, self.text, self.status_code = url, body, status
    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, response):
        self.response = response
    def get(self, *_args, **_kwargs):
        return self.response


def test_fetch_validates_and_extracts():
    body = """<html><head><title>G.Skill Aegis 32 Go DDR4 2666 MHz CL19 - Mémoire PC</title></head>
      <body><h1>G.Skill Aegis 32 Go DDR4 2666 MHz CL19</h1><table><tr><th>Capacité</th><td>32 Go</td></tr></table></body></html>"""
    client = CatalogueClient("https://catalog.test/recherche/{code}", threshold=.5)
    client.session = FakeSession(FakeResponse("https://catalog.test/fiche/PB1.html", body))
    sheet = client.fetch("S0106238", "G.Skill Aegis 32 Go DDR4 2666 MHz CL19")
    assert sheet.url.endswith("PB1.html")
    assert sheet.specifications["Capacité"] == "32 Go"
    assert sheet.match_score >= .8


def test_fetch_rejects_wrong_product():
    client = CatalogueClient("https://catalog.test/recherche/{code}", threshold=.6)
    client.session = FakeSession(FakeResponse("https://catalog.test/fiche/W.html", "<h1>Processeur 16 cœurs</h1><table><tr><th>Socket</th><td>X</td></tr></table>"))
    with pytest.raises(ProductMismatch):
        client.fetch("X", "Câble réseau 3 mètres")


def test_fetch_rejects_sheet_without_specs():
    client = CatalogueClient("https://catalog.test/recherche/{code}", threshold=.5)
    client.session = FakeSession(FakeResponse("https://catalog.test/fiche/X.html", "<h1>Câble réseau 3 mètres</h1>"))
    with pytest.raises(CatalogueError, match="caractéristique"):
        client.fetch("X", "Câble réseau 3 mètres")
