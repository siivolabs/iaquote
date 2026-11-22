# IAQuote V0.3.2

IAQuote importe un catalogue CSV dans SQLite, enrichit chaque article à partir de son **code article**, puis n'autorise la création d'un devis qu'avec des fiches vérifiées.

## Correctif GitHub Actions

Cette archive corrige le blocage `ModuleNotFoundError: No module named 'app'` de quatre façons complémentaires :

1. `app/__init__.py` expose officiellement `create_app` ;
2. le dépôt est un paquet Python installable avec `pyproject.toml` ;
3. la CI installe le projet (`pip install -e .`) et lance `python -m pytest` ;
4. `pytest.ini` et `tests/conftest.py` garantissent aussi l'import depuis un checkout non installé.

La CI vérifie explicitement `from app import create_app` depuis `/tmp`, hors de la racine du dépôt.

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
unzip iaquote-v0.3.2-github-actions.zip
cd iaquote
git init                         # uniquement pour un nouveau clone vide
git add .
git commit -m "fix: package app and restore passing CI"
git branch -M main
git remote add origin git@github.com:siivolabs/iaquote.git
git push -u origin main
```

Dans un clone existant, copiez le **contenu** du dossier `iaquote/` à la racine, vérifiez `git diff`, puis committez et poussez.

## Docker / Dockge

Copier `.env.example` vers `.env`, remplacer `SECRET_KEY`, puis :

```bash
docker compose up -d --build
docker compose exec iaquote python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8094/api/health').read().decode())"
```

Variables principales :

```env
DATABASE_PATH=/data/devisia.sqlite
CATALOG_SEARCH_BASE_URL=https://www.ldlc.com/recherche/{code}
ENRICHMENT_DELAY_SECONDS=2.5
HTTP_TIMEOUT=20
```

**Conservez le volume `iaquote_data`** lors d'une mise à niveau. Le démarrage n'efface aucune table et n'exécute aucune migration destructive.

## Test réseau facultatif sur le NAS

Le réseau externe n'est pas testé en CI. Pour vérifier les dix références déjà validées depuis le conteneur/NAS :

```bash
RUN_NETWORK_TESTS=1 python scripts/test_direct_catalog_search.py
```
