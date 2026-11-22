def test_verified_desktop_quote(client):
    response = client.post("/api/quotes", json={"customer_name": "Client", "kind": "desktop", "codes": ["DEMO-CPU-01", "DEMO-MB-01"]})
    assert response.status_code == 201
    quote = response.get_json()["quote"]
    assert quote["kind"] == "desktop" and len(quote["items"]) == 2 and quote["total"] > 0


def test_verified_laptop_quote(client):
    response = client.post("/api/quotes", json={"kind": "laptop", "codes": ["DEMO-LAPTOP-01"]})
    assert response.status_code == 201
    assert response.get_json()["quote"]["total"] == 999.0


def test_quote_rejects_unknown_code(client):
    response = client.post("/api/quotes", json={"codes": ["UNKNOWN"]})
    assert response.status_code == 400 and "absents" in response.get_json()["error"]


def test_quote_rejects_unverified_code(client):
    client.post("/api/enrich/manual", json={"code": "UNVERIFIED", "designation": "Unverified", "status": "review"})
    response = client.post("/api/quotes", json={"codes": ["UNVERIFIED"]})
    assert response.status_code == 400 and "non vérifiées" in response.get_json()["error"]


def test_quote_history(client):
    created = client.post("/api/quotes", json={"codes": ["DEMO-CPU-01"]}).get_json()["quote"]
    assert client.get(f"/api/quotes/{created['id']}").status_code == 200
    assert len(client.get("/api/quotes").get_json()["items"]) == 1
