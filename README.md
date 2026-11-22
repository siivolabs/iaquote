# DevisIA

DevisIA est une application web de préparation de devis informatiques. Elle importe un catalogue CSV privé, applique des contrôles déterministes de compatibilité, puis utilise un modèle Ollama pour rédiger un argumentaire commercial sans lui déléguer les prix, les marges ni les décisions techniques.

## Fonctions

- import CSV avec détection du séparateur et de l'encodage ;
- catalogue local SQLite, stocks, prix et marges ;
- enrichissement technique depuis une source HTTPS configurée en privé ;
- sélection pour PC fixe ou portable ;
- contrôles de socket, mémoire, format, dimensions, refroidissement, alimentation et budget ;
- argumentaire Ollama avec repli local si le modèle est indisponible ;
- références copiables et historique des propositions ;
- image Docker publiable automatiquement dans GHCR.

## Confidentialité du dépôt public

Le dépôt ne contient aucun catalogue réel, prix d'achat, marge, stock, historique, nom de collaborateur, adresse privée ni adresse de fournisseur. Le fichier `examples/catalogue-demo.csv` est entièrement synthétique et sert uniquement aux tests.

Ne versionnez jamais :

- `.env` ;
- les fichiers CSV réels ;
- le dossier `data/` ;
- les bases SQLite ;
- les journaux et exports de production.

Ces éléments sont couverts par `.gitignore` et `.dockerignore`.

## Démarrage local

```bash
cp .env.example .env
# Modifiez au minimum SECRET_KEY et OLLAMA_BASE_URL.
docker compose up -d --build
```

L'interface est ensuite disponible sur `http://adresse-du-nas:8094`.

Au premier démarrage, la base est vide. Importez le catalogue réel depuis l'onglet **Catalogue**. Les colonnes minimales sont :

- `Code article`
- `Désignation`
- `Catégorie`
- `Prix TTC €`

Les autres colonnes reconnues sont documentées par le fichier synthétique `examples/catalogue-demo.csv`.

## Configuration privée de la source technique

Toutes les adresses externes restent dans `.env`, jamais dans le code ou la documentation publiée.

```dotenv
TECHNICAL_SEARCH_URL=<modele-https-contenant-{query}>
TECHNICAL_ALLOWED_HOSTS=<domaine-autorise>
TECHNICAL_PRODUCT_PATH_MARKER=/fiche/
TECHNICAL_LINK_SELECTOR=a[href]
TECHNICAL_REQUEST_DELAY=2.5
TECHNICAL_TIMEOUT=25
```

`TECHNICAL_ALLOWED_HOSTS` accepte une liste séparée par des virgules. L'application refuse les schémas non HTTPS, les domaines absents de cette liste et les redirections vers un domaine non autorisé.

## Déploiement GitHub, GHCR et Dockge

1. Créez un dépôt GitHub vide nommé `devisia`.
2. Décompressez l'archive et publiez son contenu sur la branche `main`.
3. L'action `.github/workflows/container.yml` exécute les tests puis publie `ghcr.io/<compte>/devisia:latest`.
4. Dans `compose.dockge.yml`, remplacez `VOTRE_COMPTE` par le propriétaire du dépôt.
5. Dans Dockge, créez la stack avec le contenu de `compose.dockge.yml` et un fichier `.env` privé adapté au NAS.
6. Si l'image est privée, configurez l'authentification GHCR sur l'hôte Docker ou rendez le package public dans GitHub.

## Tests

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt pytest
pytest -q
```

Le jeu de démonstration valide l'import, le devis fixe, le devis portable, les compatibilités et le mode de secours sans IA.

## Contrôles avant publication

```bash
# Le résultat doit être vide.
grep -RIn --exclude-dir=.git '<nom-interdit>' .

# Vérifier qu'aucune donnée locale n'est suivie.
git status --short
git check-ignore -v .env data/devisia.sqlite catalogue-prive.csv
```

Remplacez `<nom-interdit>` par tout terme ou domaine qui ne doit pas apparaître dans le dépôt public.

## Limites

- La sélection reste un moteur de règles simple : le vendeur valide toujours la proposition finale.
- Les fiches techniques incertaines sont marquées **À contrôler**.
- La recherche automatique dépend de la structure HTML et des conditions d'utilisation de la source configurée.
- L'application n'intègre pas d'authentification utilisateur ; exposez-la uniquement sur un réseau de confiance ou derrière un proxy d'authentification.

## Licence

MIT — voir `LICENSE`.
