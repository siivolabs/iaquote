# Journal des versions

## 0.3.1 — 2026-11-22

- découverte de fiches par moteur HTML, API JSON configurable et fournisseur de repli ;
- validation HTTPS stricte des domaines de recherche et des fiches techniques ;
- reprise des tâches `running` et `blocked` après redémarrage ;
- réutilisation d’une tâche active au lieu de créer des tâches vides `0/0` ;
- sélection atomique des tâches et produits pour éviter les doubles traitements ;
- progression fondée sur le catalogue réel et les enrichissements effectivement enregistrés ;
- relance possible des produits en échec sans migration ni suppression de données SQLite ;
- version de santé mise à jour vers `0.3.1`.

## 0.3.0 — 2026-11-22

- enrichissement automatique de tous les produits nouveaux ou modifiés après chaque import CSV ;
- file SQLite persistante, traitement en arrière-plan et reprise après redémarrage ;
- suivi de progression avec compteurs vérifiés, à contrôler, en échec et restants ;
- arrêt d'une tâche et relance globale de tout ce qui manque ;
- conservation du cache des fiches déjà vérifiées et invalidation ciblée en cas de changement technique ;
- interface sombre par défaut, thème clair facultatif et préférence mémorisée ;
- identité visible de l'interface mise à jour vers IAQuote.

## 0.2.0 — 2026-11-22

- projet renommé DevisIA ;
- suppression des données commerciales, identités et adresses spécifiques ;
- source technique déplacée vers une configuration privée et validée par liste de domaines ;
- ajout d'un catalogue de démonstration synthétique ;
- ajout du déploiement GHCR et du Compose Dockge ;
- mise à jour des tests et de la documentation publique.
