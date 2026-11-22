# Changelog

## 0.3.2 — correctif GitHub Actions

- Rend `app` importable comme paquet Python et expose `create_app`.
- Installe le projet avant les tests et utilise `python -m pytest`.
- Ajoute une vérification d'import hors de la racine du dépôt.
- Conserve la recherche directe par code article et la chaîne redirection → validation → extraction → SQLite.
- Conserve l'import idempotent, les tâches récupérables, les statistiques globales, les devis vérifiés et le thème sombre.
