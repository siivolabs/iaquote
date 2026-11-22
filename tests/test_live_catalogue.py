import json
import os
from pathlib import Path
import pytest
from app.catalog_client import CatalogueClient

pytestmark = pytest.mark.live


@pytest.mark.skipif(os.getenv("RUN_LIVE_TESTS") != "1", reason="test réseau explicite uniquement")
def test_ten_real_references_resolve_match_and_have_specs():
    base = os.environ["CATALOG_SEARCH_BASE_URL"]
    products = json.loads((Path(__file__).parent / "fixtures" / "live_references.json").read_text())
    client = CatalogueClient(base, threshold=float(os.getenv("CATALOG_MATCH_THRESHOLD", ".58")), delay=.25)
    failures = []
    for product in products:
        try:
            sheet = client.fetch(product["code"], product["designation"])
            assert "/fiche/" in sheet.url
            assert sheet.match_score >= client.threshold
            assert sheet.specifications
        except Exception as exc:
            failures.append(f"{product['code']}: {exc}")
    assert not failures, "\n".join(failures)
