import json
import re

from . import ollama
from .db import transaction, utcnow

DESKTOP_CATEGORIES = [
    "Processeur", "Carte mère", "Mémoire PC", "Disque SSD",
    "Carte graphique", "Alimentation PC", "Boîtier PC", "Ventilateur processeur",
]
GPU_WORDS = {"jeu", "jeux", "jouer", "gaming", "dofus", "forza", "flight simulator", "1440p", "4k", "ray tracing", "blender", "3d"}
CPU_WORDS = {"dofus", "simulation", "flight simulator", "virtualisation", "compilation", "cpu"}
LAPTOP_WORDS = {"portable", "laptop", "mobilité", "nomade"}


def _load_products(db_path, categories, verified_only=True):
    placeholders = ",".join("?" for _ in categories)
    sql = f"""SELECT p.*, e.status AS enrichment_status, e.confidence, e.source_url,
                     e.specs_json, e.validation_note
              FROM products p LEFT JOIN enrichments e ON e.product_code=p.code
              WHERE p.category IN ({placeholders}) AND p.obsolete=0
                AND (p.stock_central + p.stock_shop + p.orders_pending) > 0"""
    params = list(categories)
    if verified_only:
        sql += " AND e.status='verified'"
    with transaction(db_path) as con:
        rows = con.execute(sql, params).fetchall()
    products = []
    for row in rows:
        item = dict(row)
        item["specs"] = json.loads(item.pop("specs_json") or "{}")
        products.append(item)
    return products


def _score(product, strategy):
    price = product["sale_price_ttc"]
    stock_shop = max(0, product["stock_shop"])
    stock_central = max(0, product["stock_central"])
    margin_rate = product["margin_rate"]
    sales = product["sales_28d"]
    verified = 1 if product.get("enrichment_status") == "verified" else 0
    if strategy == "margin":
        return margin_rate * 2 + product["margin_ht"] / 10 + verified * 30
    if strategy == "stock":
        return stock_shop * 100 + min(stock_central, 100) + verified * 50 - price / 100
    if strategy == "value":
        return verified * 100 + sales * 3 - price / 10
    return verified * 120 + (1 if stock_shop else 0) * 35 + min(stock_central, 100) / 5 + margin_rate / 3 + sales - price / 15


def _best(items, strategy, predicate=lambda x: True):
    choices = [p for p in items if predicate(p)]
    return max(choices, key=lambda p: _score(p, strategy)) if choices else None


def _socket(value):
    text = str(value or "").upper()
    match = re.search(r"(?:AM|LGA)\s*\d+[A-Z]*", text)
    return match.group(0).replace(" ", "") if match else text.strip()


def _memory(value):
    match = re.search(r"DDR[345]", str(value or "").upper())
    return match.group(0) if match else str(value or "").upper().strip()


def _choose_platform(products, strategy):
    cpus = [p for p in products if p["category"] == "Processeur"]
    boards = [p for p in products if p["category"] == "Carte mère"]
    memory = [p for p in products if p["category"] == "Mémoire PC"]
    combos = []
    for cpu in cpus:
        cpu_socket = _socket(cpu["specs"].get("socket"))
        cpu_mem = _memory(cpu["specs"].get("memory_type"))
        for board in boards:
            board_socket = _socket(board["specs"].get("socket"))
            board_mem = _memory(board["specs"].get("memory_type"))
            if cpu_socket and board_socket and cpu_socket != board_socket:
                continue
            for ram in memory:
                ram_mem = _memory(ram["specs"].get("memory_type"))
                if board_mem and ram_mem and board_mem != ram_mem:
                    continue
                if cpu_mem and ram_mem and cpu_mem != ram_mem:
                    continue
                combos.append((cpu, board, ram))
    if not combos:
        return [_best(cpus, strategy), _best(boards, strategy), _best(memory, strategy)]
    return list(max(combos, key=lambda combo: sum(_score(p, strategy) for p in combo)))


def _component_reason(product, need):
    category = product["category"]
    need_l = need.lower()
    reasons = {
        "Processeur": "Le processeur est cohérent avec la plateforme vérifiée et les usages demandés.",
        "Carte mère": "La carte mère correspond au socket et au type de mémoire contrôlés.",
        "Mémoire PC": "La mémoire correspond au standard accepté par la carte mère.",
        "Disque SSD": "Le SSD offre un stockage réactif avec une interface technique identifiée.",
        "Carte graphique": "La carte graphique répond au besoin graphique exprimé, sous réserve du niveau de performance attendu.",
        "Alimentation PC": "La puissance, la norme et la connectique sont adaptées aux composants sélectionnés.",
        "Boîtier PC": "Les formats et dégagements du boîtier couvrent la carte mère, le GPU et le refroidissement retenus.",
        "Ventilateur processeur": "La compatibilité socket et la capacité de dissipation sont contrôlées face au processeur.",
        "PC portable": "Ce portable correspond au type de machine demandé et utilise une fiche technique vérifiée.",
    }
    if category == "Processeur" and any(word in need_l for word in CPU_WORDS):
        return "Priorité donnée au processeur car le besoin décrit est sensible aux performances CPU."
    return reasons.get(category, "Produit issu du catalogue importé et disponible.")


def _check(name, ok, detail):
    return {"name": name, "status": "pass" if ok is True else "fail" if ok is False else "unknown", "detail": detail}


def compatibility_checks(lines):
    by_cat = {line["category"]: line for line in lines}
    checks = []
    cpu, board, ram = by_cat.get("Processeur"), by_cat.get("Carte mère"), by_cat.get("Mémoire PC")
    case, gpu = by_cat.get("Boîtier PC"), by_cat.get("Carte graphique")
    cooler, psu = by_cat.get("Ventilateur processeur"), by_cat.get("Alimentation PC")

    if cpu and board:
        a, b = _socket(cpu["specs"].get("socket")), _socket(board["specs"].get("socket"))
        checks.append(_check("Socket processeur / carte mère", a == b if a and b else None, f"CPU {a or '?'} / carte mère {b or '?'}"))
    if board and ram:
        a, b = _memory(board["specs"].get("memory_type")), _memory(ram["specs"].get("memory_type"))
        checks.append(_check("Mémoire / carte mère", a == b if a and b else None, f"Carte mère {a or '?'} / mémoire {b or '?'}"))
    if board and case:
        fmt = str(board["specs"].get("motherboard_format", "")).upper()
        formats = [str(x).upper() for x in case["specs"].get("case_formats", [])]
        checks.append(_check("Format carte mère / boîtier", fmt in formats if fmt and formats else None, f"{fmt or '?'} dans {', '.join(formats) or '?'}"))
    if gpu and case:
        length, maximum = gpu["specs"].get("gpu_length_mm"), case["specs"].get("case_max_gpu_mm")
        checks.append(_check("Longueur GPU / boîtier", float(length) <= float(maximum) if length and maximum else None, f"{length or '?'} mm / maximum {maximum or '?'} mm"))
    if cooler and case:
        height, maximum = cooler["specs"].get("cooler_height_mm"), case["specs"].get("case_max_cooler_mm")
        checks.append(_check("Hauteur ventirad / boîtier", float(height) <= float(maximum) if height and maximum else None, f"{height or '?'} mm / maximum {maximum or '?'} mm"))
    if cooler and cpu:
        sockets = [_socket(x) for x in cooler["specs"].get("supported_sockets", [])]
        cpu_socket = _socket(cpu["specs"].get("socket"))
        socket_ok = cpu_socket in sockets if cpu_socket and sockets else None
        cooler_tdp, cpu_tdp = cooler["specs"].get("cooler_tdp_w"), cpu["specs"].get("required_cooling_w") or cpu["specs"].get("tdp_w")
        thermal_ok = float(cooler_tdp) >= float(cpu_tdp) if cooler_tdp and cpu_tdp else None
        checks.append(_check("Ventirad / socket CPU", socket_ok, f"CPU {cpu_socket or '?'} / ventirad {', '.join(sockets) or '?'}"))
        checks.append(_check("Capacité de refroidissement", thermal_ok, f"Capacité {cooler_tdp or '?'} W / besoin {cpu_tdp or '?'} W"))
    if gpu and psu:
        required, available = gpu["specs"].get("recommended_psu_w"), psu["specs"].get("power_w")
        checks.append(_check("Puissance alimentation / GPU", float(available) >= float(required) if required and available else None, f"Alimentation {available or '?'} W / recommandé {required or '?'} W"))
    return checks


def _fallback_copy(need, lines, checks):
    passed = len([c for c in checks if c["status"] == "pass"])
    unknown = len([c for c in checks if c["status"] == "unknown"])
    return {
        "argumentaire": f"Cette proposition s'appuie exclusivement sur les références du catalogue importé. Elle équilibre le besoin exprimé, la disponibilité et le budget. {passed} contrôles de compatibilité sont validés par des données techniques structurées. " + (f"{unknown} point(s) restent à contrôler avant validation finale." if unknown else "Aucun contrôle technique n'est laissé indéterminé."),
        "justifications": {line["code"]: line["reason"] for line in lines},
        "points_de_vigilance": [c["detail"] for c in checks if c["status"] != "pass"],
        "questions_client": ["Confirmer la résolution d'écran et le niveau de qualité visé.", "Confirmer les besoins d'évolution et de silence."],
    }


def generate_quote(client_need, budget_ttc, strategy="balanced", machine_type="auto", verified_only=True, db_path=None):
    need_l = client_need.lower()
    if machine_type == "auto":
        machine_type = "laptop" if any(word in need_l for word in LAPTOP_WORDS) else "desktop"

    if machine_type == "laptop":
        products = _load_products(db_path, ["PC portable"], verified_only)
        chosen = [_best(products, strategy)]
    else:
        products = _load_products(db_path, DESKTOP_CATEGORIES, verified_only)
        chosen = _choose_platform(products, strategy)
        chosen.append(_best([p for p in products if p["category"] == "Disque SSD"], strategy))
        cooler = _best([p for p in products if p["category"] == "Ventilateur processeur"], strategy)
        chosen.append(cooler)
        wants_gpu = any(word in need_l for word in GPU_WORDS)
        cpu = next((p for p in chosen if p and p["category"] == "Processeur"), None)
        board = next((p for p in chosen if p and p["category"] == "Carte mère"), None)
        integrated = bool(cpu and cpu["specs"].get("integrated_graphics"))
        gpu = _best([p for p in products if p["category"] == "Carte graphique"], strategy) if (wants_gpu or not integrated) else None
        if gpu:
            chosen.append(gpu)
        psu_candidates = [p for p in products if p["category"] == "Alimentation PC"]
        if gpu and gpu["specs"].get("recommended_psu_w"):
            needed = float(gpu["specs"]["recommended_psu_w"])
            psu = _best(psu_candidates, strategy, lambda p: float(p["specs"].get("power_w", 0)) >= needed)
        else:
            psu = _best(psu_candidates, strategy)
        chosen.append(psu)

        def case_fits(case):
            specs = case.get("specs", {})
            if board:
                fmt = str(board["specs"].get("motherboard_format", "")).upper()
                accepted = [str(x).upper() for x in specs.get("case_formats", [])]
                if fmt and accepted and fmt not in accepted:
                    return False
            if gpu and gpu["specs"].get("gpu_length_mm") and specs.get("case_max_gpu_mm"):
                if float(gpu["specs"]["gpu_length_mm"]) > float(specs["case_max_gpu_mm"]):
                    return False
            if cooler and cooler["specs"].get("cooler_height_mm") and specs.get("case_max_cooler_mm"):
                if float(cooler["specs"]["cooler_height_mm"]) > float(specs["case_max_cooler_mm"]):
                    return False
            return True

        cases = [p for p in products if p["category"] == "Boîtier PC"]
        chosen.append(_best(cases, strategy, case_fits) or _best(cases, strategy))

    chosen = [p for p in chosen if p]
    if not chosen:
        raise ValueError("Aucun produit éligible. Importez le catalogue et enrichissez les catégories nécessaires, ou désactivez le filtre 'vérifié uniquement'.")

    # Deduplicate while keeping component order.
    seen, lines = set(), []
    for product in chosen:
        if product["code"] in seen:
            continue
        seen.add(product["code"])
        line = {k: product[k] for k in ("code", "designation", "category", "brand", "sale_price_ttc", "purchase_price_ht", "margin_ht", "margin_rate", "stock_central", "stock_shop")}
        line.update({"specs": product.get("specs", {}), "source_url": product.get("source_url"), "enrichment_status": product.get("enrichment_status"), "reason": _component_reason(product, client_need)})
        lines.append(line)

    total = round(sum(line["sale_price_ttc"] for line in lines), 2)
    margin = round(sum(line["margin_ht"] for line in lines), 2)
    checks = compatibility_checks(lines)
    if total <= budget_ttc:
        checks.append(_check("Budget", True, f"Total {total:.2f} € / budget {budget_ttc:.2f} €"))
    else:
        checks.append(_check("Budget", False, f"Dépassement de {total - budget_ttc:.2f} € (total {total:.2f} €)"))

    ai_status = {"ok": False, "mode": "fallback"}
    copy = _fallback_copy(client_need, lines, checks)
    try:
        generated = ollama.generate_sales_copy(client_need, budget_ttc, lines, checks)
        if isinstance(generated, dict) and generated.get("argumentaire"):
            copy.update(generated)
            ai_status = {"ok": True, "mode": "ollama", "model": ollama.model_name()}
    except Exception as exc:
        ai_status["error"] = str(exc)

    quote = {
        "created_at": utcnow(), "client_need": client_need, "budget_ttc": round(float(budget_ttc), 2),
        "strategy": strategy, "machine_type": machine_type, "verified_only": bool(verified_only),
        "lines": lines, "total_ttc": total, "margin_ht": margin,
        "margin_rate_on_ht": round(margin / max(0.01, sum(line["purchase_price_ht"] + line["margin_ht"] for line in lines)) * 100, 2),
        "budget_delta": round(budget_ttc - total, 2), "checks": checks,
        "references": "\n".join(line["code"] for line in lines), "sales_copy": copy, "ai": ai_status,
    }
    with transaction(db_path) as con:
        cur = con.execute("INSERT INTO quotes(created_at, client_need, budget_ttc, strategy, quote_json) VALUES(?,?,?,?,?)", (quote["created_at"], client_need, budget_ttc, strategy, json.dumps(quote, ensure_ascii=False)))
        quote["id"] = cur.lastrowid
        con.execute("UPDATE quotes SET quote_json=? WHERE id=?", (json.dumps(quote, ensure_ascii=False), quote["id"]))
    return quote
