# IAQuote

IAQuote est une application web de préparation de devis informatiques. Elle importe un catalogue CSV privé, applique des contrôles déterministes de compatibilité, puis utilise un modèle Ollama pour rédiger un argumentaire commercial sans lui déléguer les prix, les marges ni les décisions techniques.

## Fonctions

- import CSV avec détection du séparateur et de l'encodage ;
- catalogue local SQLite, stocks, prix et marges ;
- enrichissement automatique de tout le catalogue, en arrière-plan, depuis une source HTTPS configurée en privé ;
- file persistante avec progression, reprise après redémarrage, arrêt et relance des échecs ;
- interface sombre par défaut avec thème clair facultatif ;
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

Au premier démarrage, la base est vide. Importez le catalogue réel depuis l'onglet **Catalogue**. L'import répond immédiatement, puis tous les produits nouveaux ou techniquement modifiés sont enrichis dans une file d'arrière-plan. Les colonnes minimales sont :

- `Code article`
- `Désignation`
- `Catégorie`
- `Prix TTC €`

Les autres colonnes reconnues sont documentées par le fichier synthétique `examples/catalogue-demo.csv`.

## Configuration privée de la source technique

Toutes les adresses externes restent dans `.env`, jamais dans le code ou la documentation publiée.

```dotenv
TECHNICAL_ALLOWED_HOSTS=<domaine-autorise>
TECHNICAL_PRODUCT_PATH_MARKER=/fiche/
TECHNICAL_LINK_SELECTOR=a[href]

# Recherche principale HTML, API JSON ou liste de replis (facultatifs).
TECHNICAL_SEARCH_URL=<modele-https-contenant-{query}>
TECHNICAL_SEARCH_API_URL=
TECHNICAL_SEARCH_FALLBACK_URLS=
TECHNICAL_SEARCH_ALLOWED_HOSTS=

# Repli indépendant intégré ; désactivez-le si une recherche privée est imposée.
TECHNICAL_DEFAULT_DISCOVERY=true
TECHNICAL_SEARCH_STOP_SCORE=0.72
TECHNICAL_SEARCH_DELAY=2.5
TECHNICAL_SEARCH_TIMEOUT=25
TECHNICAL_REQUEST_DELAY=2.5
TECHNICAL_TIMEOUT=25
AUTO_ENRICH_ON_IMPORT=true
```

Les modèles de recherche acceptent `{query}` (encodé), `{domain}` (premier domaine autorisé) et `{raw_query}` (brut). `TECHNICAL_ALLOWED_HOSTS` et `TECHNICAL_SEARCH_ALLOWED_HOSTS` acceptent des listes séparées par des virgules. L’application refuse les schémas non HTTPS, les domaines absents des listes autorisées et les redirections non autorisées. Les fiches retenues doivent aussi correspondre à `TECHNICAL_PRODUCT_PATH_MARKER`.

## Enrichissement automatique d'un catalogue complet

- Chaque import planifie tous les articles sans fiche technique, sans demander de saisie manuelle.
- Les fiches déjà vérifiées restent en cache. Une modification de la désignation, de la marque ou de la catégorie invalide uniquement la fiche concernée.
- Le traitement est séquentiel et respecte `TECHNICAL_REQUEST_DELAY` pour ne pas solliciter excessivement la source.
- Un échec est enregistré pour le produit concerné, mais les produits suivants continuent d'être traités.
- La tâche et sa progression sont stockées dans SQLite. Après un redémarrage du conteneur, une tâche interrompue reprend automatiquement.
- L'onglet **Enrichissement** permet de suivre les produits vérifiés, à contrôler, en échec et restants, puis de relancer tout ce qui manque.

## Déploiement GitHub, GHCR et Dockge

1. Créez un dépôt GitHub vide nommé `iaquote`.
2. Décompressez l’archive puis publiez le contenu du dossier `iaquote/` à la racine de la branche `main`.
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
