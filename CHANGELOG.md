# Changelog

## 0.3.2 — compatibilité des tâches SQLite V0.3.1

- Supprime toute dépendance à une colonne `id` dans `enrichment_job_items`.
- Lit et traite les anciennes tâches utilisant la clé composée `(job_id, product_code)`.
- Ajoute des régressions HTTP pour `/enrichment` et `/api/enrich/jobs/current` sur une base héritée.
- Vérifie également la reprise du worker sur ce schéma historique.
- Publie l'image Docker dans GHCR après réussite des tests sur la branche `main`.
- Conserve le volume SQLite, l'interface sombre et la recherche directe par code configurée par l'environnement.

## 0.3.2 — correctif GitHub Actions

- Rend `app` importable comme paquet Python et expose `create_app`.
- Installe le projet avant les tests et utilise `python -m pytest`.
- Ajoute une vérification d'import hors de la racine du dépôt.
- Conserve la recherche directe par code article et la chaîne redirection → validation → extraction → SQLite.
- Conserve l'import idempotent, les tâches récupérables, les statistiques globales, les devis vérifiés et le thème sombre.
