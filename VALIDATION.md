# Validation de livraison — IAQuote V0.3.2

Date : 22 novembre 2026

## Incidents corrigés

La base SQLite en production possède une table `enrichment_jobs` antérieure à
V0.3.2, sans colonne `updated_at`. `POST /api/enrich/jobs` échouait donc avant
même la création d'une tâche. Le démarrage applique maintenant une migration
atomique qui ajoute et renseigne cette colonne, ainsi que les autres colonnes
requises par V0.3.2.

La base SQLite créée par V0.3.1 possède une table `enrichment_job_items` identifiée par la clé composée `(job_id, product_code)` et sans colonne `id`. V0.3.2 utilisait pourtant `i.id` dans `get_bulk_job()` et dans le worker, ce qui provoquait :

- une réponse HTTP 500 sur `/api/enrich/jobs/current` ;
- l'échec de la page `/enrichment` ;
- l'impossibilité de reprendre certaines anciennes tâches.

La livraison ne dépend plus de cette colonne. La lecture, le tri et les mises à jour utilisent uniquement les colonnes présentes dans les deux schémas. Aucune reconstruction de table et aucune suppression de données ne sont exécutées.

## Contrôles exécutés

- 38 tests de régression : réussis ;
- migration d'un schéma V0.3.1 réel sans `enrichment_jobs.updated_at` : réussie ;
- `POST /api/enrich/jobs` sur cette base : HTTP 201 ;
- enrichissement de `S0111966` par code, redirection vers une fiche, extraction et écriture SQLite : réussi ;
- seconde et troisième exécutions des migrations : aucune duplication et aucune perte ;
- page `/enrichment` avec une ancienne tâche sans colonne `id` : HTTP 200 ;
- API `/api/enrich/jobs/current` sur la même base : HTTP 200 et ancien élément retourné ;
- reprise complète du worker sur le schéma V0.3.1 : réussie ;
- démarrage sur un catalogue existant de 291 produits : 291 produits et ancienne erreur conservés ;
- compilation de tous les fichiers Python : réussie ;
- construction du wheel Python : réussie ;
- modèles Jinja et feuille de style sombre inclus dans le wheel ;
- import installable `from app import create_app` conservé ;
- workflow GitHub Actions : tests avant construction, publication GHCR seulement sur `push` vers `main` ;
- absence de base SQLite dans la livraison ;
- absence de `DROP TABLE`, de suppression de lignes et de commande supprimant la base ou le volume dans le code d'exécution ;
- absence du nom de l'enseigne dans les fichiers publiés.

## Périmètre réseau

Les tests déterministes ne contactent aucun catalogue externe. La recherche directe par code est fournie par `CATALOG_SEARCH_BASE_URL` et requiert le marqueur `{code}` dans le modèle recommandé. Le contrôle facultatif de dix références se lance sur le NAS avec :

```bash
RUN_NETWORK_TESTS=1 python scripts/test_direct_catalog_search.py
```

## Environnement de validation

Le miroir de paquets de l'environnement de fabrication ne proposait pas Flask. La suite complète a donc été exécutée avec un petit adaptateur compatible Flask placé hors du dépôt et absent de l'archive. Les 38 tests, y compris les routes, les modèles Jinja, les imports CSV, les devis et les régressions SQLite, ont réussi. GitHub Actions réinstalle Flask réel depuis `requirements-dev.txt` avant d'exécuter la même commande `python -m pytest -q`.
