#!/usr/bin/env python3
import json
import os
import sys
import urllib.request

BASE = os.getenv("APP_URL", "http://127.0.0.1:8094").rstrip("/")


def request(path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(
        BASE + path,
        data=data,
        headers=headers,
        method="POST" if data else "GET",
    )
    with urllib.request.urlopen(req, timeout=300) as response:
        return response.status, json.loads(response.read())


def main():
    status, health = request("/api/health")
    assert status == 200 and health["ok"] is True
    if health["catalog_count"] == 0:
        print(json.dumps({"ok": True, "health": health, "note": "Catalogue vide : import requis."}, ensure_ascii=False, indent=2))
        return
    status, quote = request("/api/quotes", {
        "client_need": "PC fixe pour jouer en 1080p, silencieux et évolutif.",
        "budget_ttc": 1600,
        "strategy": "balanced",
        "machine_type": "desktop",
        "verified_only": True,
    })
    assert status == 201
    assert quote["references"]
    print(json.dumps({
        "ok": True,
        "health": health,
        "quote_id": quote["id"],
        "total_ttc": quote["total_ttc"],
        "ai": quote["ai"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise
