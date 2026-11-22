# IAQuote V0.3.2

IAQuote importe un catalogue CSV dans SQLite, enrichit chaque article à partir de son **code article**, puis n'autorise la création d'un devis qu'avec des fiches vérifiées.

## Correctifs de compatibilité SQLite

Cette livraison corrige le blocage observé sur une base existante :

```text
sqlite3.OperationalError: table enrichment_jobs has no column named updated_at
```

Au démarrage, `init_db()` exécute désormais des migrations **transactionnelles,
additives et idempotentes**. Les versions appliquées sont enregistrées dans
`iaquote_schema_migrations` et dans `PRAGMA user_version`. La mise à niveau :

- ajoute `updated_at` et les autres colonnes V0.3.2 manquantes ;
- renseigne les nouvelles colonnes à partir des anciens horodatages et champs ;
- conserve toutes les colonnes historiques, les lignes et les identifiants ;
- les migrations d'exécution ne contiennent aucun `DROP TABLE`, `DELETE` ou remplacement du fichier SQLite ;
- fonctionne aussi bien sur une installation neuve que sur les schémas V0.3.0/V0.3.1.

Cette archive corrige le blocage `sqlite3.OperationalError: no such column: i.id` rencontré avec les bases créées par V0.3.1. Le code n'utilise plus de colonne `id` dans `enrichment_job_items` : il s'appuie sur la clé historique `(job_id, product_code)`, y compris pendant l'exécution du worker.

Elle conserve aussi le correctif `ModuleNotFoundError: No module named 'app'` de quatre façons complémentaires :

1. `app/__init__.py` expose officiellement `create_app` ;
2. le dépôt est un paquet Python installable avec `pyproject.toml` ;
3. la CI installe le projet (`pip install -e .`) et lance `python -m pytest` ;
4. `pytest.ini` et `tests/conftest.py` garantissent aussi l'import depuis un checkout non installé.

La CI vérifie explicitement `from app import create_app` depuis `/tmp`, hors de la racine du dépôt.

Les régressions recréent également le schéma V0.3.1 réel, notamment
`enrichment_jobs` sans `updated_at`, `enrichment_job_items` sans `id`, l'ancien
catalogue, les enrichissements et les devis. Elles vérifient la conservation de
291 produits, des tâches et de leurs erreurs, puis exécutent `POST
/api/enrich/jobs` et la chaîne code article → redirection → extraction → SQLite
pour `S0111966`.

## Fonctions conservées

- Import CSV flexible et idempotent ;
- catalogue, tableau de bord, devis et historique ;
- recherche directe par code via `CATALOG_SEARCH_BASE_URL` ;
- suivi des redirections, comparaison titre/désignation et extraction de caractéristiques ;
- stockage de la source, du titre, du score, des caractéristiques et des erreurs ;
- traitements séquentiels temporisés, reprise après redémarrage et isolation des erreurs ;
- statistiques globales basées sur le catalogue, sans tâche trompeuse `0/0` ;
- migrations SQLite uniquement additives ;
- interface sombre ;
- Docker, Compose/Dockge et contrôle `/api/health`.
- compatibilité en lecture et en traitement avec les anciennes tâches SQLite sans migration destructive.

## Développement et tests

Prérequis : Python 3.11 ou 3.12.

```bash
python -m venv .venv
. .venv/bin/activate                  # Windows PowerShell : .venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt -e .
python -m pytest -q
```

Vérification équivalente à GitHub Actions :

```bash
cd /tmp
python -c "from app import create_app; assert callable(create_app)"
```

## Démarrage local

```bash
export DATABASE_PATH="$PWD/data/devisia.sqlite"
export BOOTSTRAP_CSV="$PWD/examples/catalogue-demo.csv"
export PRESEED_PATH="$PWD/examples/enrichissement-demo.json"
python run.py
```

Ouvrir <http://127.0.0.1:8094>. Le contrôle de santé est disponible sur `/api/health` et doit renvoyer :

```json
{"database":"ok","status":"ok","version":"0.3.2"}
```

## Publication dans `siivolabs/iaquote`

L'archive contient un dossier `iaquote/` représentant la racine complète du dépôt.

```bash
unzip iaquote-v0.3.2-migrations-complet.zip
cd iaquote
git init                         # uniquement pour un nouveau clone vide
git add .
git commit -m "fix: package app and restore passing CI"
git branch -M main
git remote add origin git@github.com:siivolabs/iaquote.git
git push -u origin main
```

Dans un clone existant, remplacez le contenu suivi par Git avec le **contenu** du dossier `iaquote/`, puis committez et poussez l'ensemble. Aucun correctif de code séparé n'est nécessaire.

## Docker / Dockge

Copier `.env.example` vers `.env`, remplacer `SECRET_KEY`, puis :

```bash
docker compose up -d --build
docker compose exec iaquote python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8094/api/health').read().decode())"
```

Pour Dockge, `docker-compose.yml` utilise directement l'image publiée. `compose.yaml` conserve en plus `build: .` pour un lancement local depuis le dépôt.

Variables principales :

```env
DATABASE_PATH=/data/devisia.sqlite
CATALOG_SEARCH_BASE_URL=https://catalogue.example/recherche/{code}
ENRICHMENT_DELAY_SECONDS=2.5
HTTP_TIMEOUT=20
```

Remplacez le domaine d'exemple par l'endpoint de recherche directe déjà configuré dans Dockge. Le marqueur `{code}` est obligatoire dans le modèle recommandé.

**Conservez le volume `iaquote_data`** lors d'une mise à niveau. Le démarrage n'efface aucune table et n'exécute aucune migration destructive. Les 291 produits et les anciennes tâches restent dans le même fichier `/data/devisia.sqlite`.

Le workflow CI teste le paquet et l'image, puis publie `ghcr.io/siivolabs/iaquote:latest` uniquement pour un `push` sur `main`. Les pull requests construisent l'image sans la publier.

## Test réseau facultatif sur le NAS

Le réseau externe n'est pas testé en CI. Pour vérifier les dix références déjà validées depuis le conteneur/NAS :

```bash
RUN_NETWORK_TESTS=1 python scripts/test_direct_catalog_search.py
```
