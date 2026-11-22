import json
import os
import re

import requests


def base_url():
    return os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")


def model_name():
    return os.getenv("OLLAMA_MODEL", "qwen3:32b")


def timeout():
    return int(os.getenv("OLLAMA_TIMEOUT", "240"))


def health():
    try:
        response = requests.get(f"{base_url()}/api/tags", timeout=4)
        response.raise_for_status()
        names = [m.get("name") for m in response.json().get("models", [])]
        return {"ok": True, "base_url": base_url(), "model": model_name(), "model_present": model_name() in names, "models": names[:20]}
    except Exception as exc:
        return {"ok": False, "base_url": base_url(), "model": model_name(), "error": str(exc)}


def _extract_json(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


def generate_sales_copy(client_need, budget, lines, checks):
    products = []
    send_costs = os.getenv("SEND_COSTS_TO_OLLAMA", "false").lower() == "true"
    for line in lines:
        item = {
            "code": line["code"], "category": line["category"],
            "designation": line["designation"], "price_ttc": line["sale_price_ttc"],
            "verified_specs": line.get("specs", {}),
        }
        if send_costs:
            item["margin_ht"] = line.get("margin_ht", 0)
        products.append(item)
    prompt = f"""Tu es un conseiller informatique en boutique. Tu n'inventes aucune référence, prix, compatibilité ou performance.
Besoin client : {client_need}
Budget TTC : {budget:.2f} EUR
Produits sélectionnés (seule source autorisée) :
{json.dumps(products, ensure_ascii=False)}
Contrôles déterministes :
{json.dumps(checks, ensure_ascii=False)}

Réponds uniquement en JSON valide sous cette forme :
{{"argumentaire":"3 à 6 phrases sobres et commerciales", "justifications":{{"CODE":"raison factuelle en une phrase"}}, "points_de_vigilance":["..."], "questions_client":["..."]}}
Ne révèle ni prix d'achat ni marge. Ne transforme jamais un contrôle inconnu en contrôle validé."""
    payload = {
        "model": model_name(), "stream": False,
        "messages": [{"role": "user", "content": prompt}],
        "options": {"temperature": 0.2},
        "format": "json",
    }
    response = requests.post(f"{base_url()}/api/chat", json=payload, timeout=timeout())
    response.raise_for_status()
    content = response.json().get("message", {}).get("content", "")
    return _extract_json(content)
