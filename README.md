# IAQuote 0.3.2

Application locale de catalogue, devis et enrichissement de fiches techniques.

## Nouveautés 0.3.2

- découverte directe par code article via un endpoint de recherche configurable ;
- validation du titre de la fiche par rapport à la désignation du CSV ;
- extraction structurée des caractéristiques et stockage SQLite ;
- reprise au démarrage des traitements interrompus et relance des échecs ;
- statistiques calculées depuis la base et progression actualisée dans l'interface ;
- migrations additives : les tables et données existantes ne sont jamais supprimées.

## Déploiement Docker / Dockge

1. Copiez `.env.example` vers `.env` sur l'hôte (ne publiez pas ce fichier).
2. Renseignez `CATALOG_SEARCH_BASE_URL` avec l'URL de recherche, terminée par `/` ou contenant `{code}`.
3. Renseignez une valeur aléatoire longue pour `SECRET_KEY`.
4. Déployez `docker-compose.yml` dans Dockge.
5. Conservez le volume nommé `iaquote_data` lors des mises à jour.

Le service est exposé sur `http://HOTE:8094` et le contrôle de santé sur `/api/health`.

## Développement

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
pytest
python run.py
```

## Tests réseau réels (optionnels)

Les tests standards sont déterministes et ne dépendent pas du réseau. Pour valider les références réelles depuis un environnement autorisé :

```bash
RUN_LIVE_TESTS=1 \
CATALOG_SEARCH_BASE_URL='https://catalogue.example/recherche/{code}' \
pytest -m live -q
```

L'endpoint réel doit être fourni au moment du test ; il n'est pas enregistré dans le dépôt.

## Import CSV

Formats de séparateur acceptés : `;`, `,` et tabulation. Colonnes reconnues :

- code : `code`, `reference`, `ref`, `sku`, `code_article` ;
- désignation : `designation`, `description`, `produit`, `libelle`, `name` ;
- prix : `prix`, `price`, `prix_ht`, `unit_price` ;
- stock : `stock`, `quantite`, `qty`, `quantity`.

Un nouvel import met à jour les produits portant le même code sans créer de doublon et peut être immédiatement placé dans la file d'enrichissement.
