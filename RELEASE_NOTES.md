# IAQuote 0.3.2

## Livraison

Version complète prête à publier à la racine du dépôt et à déployer avec Docker/Dockge.

## Changements

- Recherche prioritaire par code article avec suivi des redirections.
- Validation de la fiche trouvée par comparaison normalisée avec la désignation.
- Extraction des tableaux et listes de caractéristiques.
- Enregistrement de l’URL source, du titre, du score, des caractéristiques et des erreurs dans SQLite.
- Relance des références absentes ou précédemment échouées.
- Reprise automatique des traitements interrompus après redémarrage.
- Progression et statistiques calculées à partir des données SQLite.
- Import CSV idempotent, devis, historique et thème sombre.
- Migrations additives pour préserver les données présentes.

## Validation

- 26 tests automatisés déterministes réussis.
- Tests de bout en bout : code article → redirection → validation → extraction → SQLite.
- Jeu de 10 références réelles inclus pour le contrôle réseau volontaire sur l’environnement de déploiement.
- Contrôle de santé : version `0.3.2`, base SQLite accessible.

Le test réseau externe est volontairement séparé de la suite déterministe et nécessite `RUN_LIVE_TESTS=1` ainsi que l’endpoint fourni par variable d’environnement.
