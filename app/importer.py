import csv
import io
from pathlib import Path

from .db import transaction, utcnow

REQUIRED = {"Code article", "Désignation", "Catégorie", "Prix TTC €"}


def parse_money(value):
    if value is None:
        return 0.0
    value = str(value).replace("€", "").replace("%", "").replace("\u202f", "").replace(" ", "").replace(",", ".").strip()
    try:
        return round(float(value or 0), 4)
    except ValueError:
        return 0.0


def parse_int(value):
    try:
        return int(float(str(value or 0).replace(",", ".").strip()))
    except ValueError:
        return 0


def decode_csv(data):
    if isinstance(data, str):
        return data
    for encoding in ("utf-8-sig", "cp1252", "latin1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("Encodage CSV non reconnu")


def import_csv_bytes(data, db_path=None):
    text = decode_csv(data)
    sample = text[:12000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ";"
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    headers = set(reader.fieldnames or [])
    missing = sorted(REQUIRED - headers)
    if missing:
        raise ValueError("Colonnes obligatoires absentes : " + ", ".join(missing))

    imported = 0
    created = 0
    updated = 0
    unchanged = 0
    enrichment_codes = set()
    now = utcnow()
    with transaction(db_path) as con:
        for row in reader:
            code = (row.get("Code article") or "").strip()
            designation = (row.get("Désignation") or "").strip()
            if not code or not designation:
                continue
            category = (row.get("Catégorie") or "").strip()
            brand = (row.get("Marque") or "").strip()
            existing = con.execute(
                "SELECT designation, category, brand FROM products WHERE code=?", (code,)
            ).fetchone()
            technical_identity_changed = bool(
                existing
                and (
                    existing["designation"] != designation
                    or (existing["category"] or "") != category
                    or (existing["brand"] or "") != brand
                )
            )
            if existing is None:
                created += 1
                enrichment_codes.add(code)
            elif technical_identity_changed:
                updated += 1
                enrichment_codes.add(code)
                # Une fiche mise en cache ne doit pas survivre à un changement de
                # désignation, de marque ou de catégorie.
                con.execute("DELETE FROM enrichments WHERE product_code=?", (code,))
            else:
                unchanged += 1
                enriched = con.execute(
                    "SELECT 1 FROM enrichments WHERE product_code=?", (code,)
                ).fetchone()
                if not enriched:
                    enrichment_codes.add(code)
            con.execute(
                """
                INSERT INTO products (
                  code, family_code, designation, category, brand, reference_type,
                  purchase_price_ht, margin_ht, margin_rate, crossed_price, sale_price_ttc,
                  stock_central, stock_shop, orders_pending, obsolete,
                  sales_7d, sales_14d, sales_28d, representative, source_updated_at, imported_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(code) DO UPDATE SET
                  family_code=excluded.family_code, designation=excluded.designation,
                  category=excluded.category, brand=excluded.brand,
                  reference_type=excluded.reference_type,
                  purchase_price_ht=excluded.purchase_price_ht, margin_ht=excluded.margin_ht,
                  margin_rate=excluded.margin_rate, crossed_price=excluded.crossed_price,
                  sale_price_ttc=excluded.sale_price_ttc,
                  stock_central=excluded.stock_central, stock_shop=excluded.stock_shop,
                  orders_pending=excluded.orders_pending, obsolete=excluded.obsolete,
                  sales_7d=excluded.sales_7d, sales_14d=excluded.sales_14d,
                  sales_28d=excluded.sales_28d, representative=excluded.representative,
                  source_updated_at=excluded.source_updated_at, imported_at=excluded.imported_at
                """,
                (
                    code, (row.get("Code famille") or "").strip(), designation,
                    category, brand,
                    (row.get("Type de référencement") or "").strip(),
                    parse_money(row.get("Prix d'achat €")), parse_money(row.get("Marge")),
                    parse_money(row.get("Indice")), parse_money(row.get("Prix barré")),
                    parse_money(row.get("Prix TTC €")), parse_int(row.get("Stock central")),
                    parse_int(row.get("Stock boutique")), parse_int(row.get("Commandes en cours")),
                    1 if (row.get("Article Obsolète") or "").strip().lower() in {"oui", "true", "1"} else 0,
                    parse_int(row.get("Ventes 7j")), parse_int(row.get("Ventes 14j")),
                    parse_int(row.get("Ventes 28j")), (row.get("Représentant") or "").strip(),
                    (row.get("Date de dernière mise à jour") or "").strip(), now,
                ),
            )
            imported += 1
    return {
        "imported": imported,
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "delimiter": delimiter,
        "headers": len(headers),
        # Réservé à l'orchestration serveur ; l'API retire cette liste avant de
        # répondre afin de ne pas renvoyer plusieurs centaines de codes.
        "enrichment_codes": sorted(enrichment_codes),
    }


def import_csv_file(path, db_path=None):
    return import_csv_bytes(Path(path).read_bytes(), db_path)
