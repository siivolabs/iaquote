# IAQuote 0.3.1 — notes de livraison

Cette archive contient l’application complète prête à être placée à la racine du dépôt. Aucun overlay, script de correction ou copie manuelle de fichier n’est nécessaire.

## Correctifs intégrés

- découverte automatique par HTML, JSON et moteur de repli ;
- validation HTTPS et liste blanche distincte pour les domaines de recherche et de fiches ;
- reprise des tâches bloquées ou interrompues ;
- réutilisation des tâches actives et absence de tâches artificielles `0/0` ;
- traitement atomique des travaux ;
- statistiques calculées depuis le catalogue et les enrichissements réellement enregistrés ;
- relance des produits en échec.

## Compatibilité et conservation

- aucun changement du schéma SQLite ;
- base persistante conservée dans `/data/devisia.sqlite` ;
- volume Docker/Dockge monté sur `/data` ;
- interface et thème sombre inchangés ;
- endpoints HTTP existants conservés.

## Déploiement

1. Extraire l’archive.
2. Placer le contenu de `iaquote/` à la racine du dépôt GitHub.
3. Commit et push sur `main`.
4. Attendre la réussite du workflow de test et de construction.
5. Dans Dockge, effectuer **Pull** puis **Recreate/Deploy** sans supprimer le volume de données.
6. Vérifier `/api/health` : la version attendue est `0.3.1`.

Avant mise en production, sauvegarder le volume ou le fichier SQLite selon la procédure d’exploitation habituelle.
