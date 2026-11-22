# Sécurité

Ne publiez pas de vulnérabilité avec des données de production. Utilisez un canal privé du propriétaire du dépôt.

## Données à ne jamais joindre à un ticket public

- catalogues, prix d'achat, marges et stocks réels ;
- fichiers `.env`, adresses privées et secrets ;
- bases SQLite, devis, historiques et journaux ;
- captures contenant des informations client ou collaborateur.

## Déploiement

DevisIA ne fournit pas d'authentification intégrée. Limitez l'accès au réseau de confiance ou placez l'application derrière un proxy d'authentification. Utilisez une valeur `SECRET_KEY` longue et aléatoire et gardez `SEND_COSTS_TO_OLLAMA=false` sauf besoin explicite maîtrisé.
