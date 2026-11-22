# Validation de livraison — IAQuote V0.3.2

Date : 22 novembre 2026

## Incident corrigé

L'import `from app import create_app` est désormais supporté depuis :

- la racine d'un checkout Git ;
- le lanceur `pytest` ;
- `python -m pytest` ;
- un emplacement extérieur au dépôt après installation du paquet.

Le correctif repose sur `app/__init__.py`, `pyproject.toml`, `pytest.ini`, le bootstrap de `tests/conftest.py` et l'installation editable dans `.github/workflows/ci.yml`.

## Contrôles exécutés

- 30 tests de régression : réussis ;
- compilation de tous les fichiers Python : réussie ;
- construction du wheel Python : réussie ;
- import du wheel depuis `/tmp` : réussi ;
- compilation syntaxique des 8 modèles Jinja : réussie ;
- lecture des exemples JSON et des fichiers YAML : réussie ;
- présence de `app/__init__.py` et des modèles dans le wheel : vérifiée ;
- URL directe `904240` : `https://www.ldlc.com/recherche/904240` ;
- absence de base SQLite dans la livraison : vérifiée ;
- absence de commande supprimant la base ou le volume : vérifiée.

## Périmètre réseau

Le test déterministe ne contacte pas le catalogue externe. Le contrôle facultatif de dix références se lance sur le NAS avec :

```bash
RUN_NETWORK_TESTS=1 python scripts/test_direct_catalog_search.py
```

## Limite de l'environnement de fabrication

Le miroir de paquets Python de l'environnement de fabrication ne proposait pas Flask. Les routes ont donc été exercées avec un adaptateur de validation placé hors de l'archive ; cet adaptateur n'est pas livré. Le workflow GitHub installe Flask réel avant de relancer les mêmes tests et de construire l'image Docker. Le statut GitHub Actions ne peut être observé qu'après publication dans le dépôt.
