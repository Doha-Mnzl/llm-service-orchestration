# Orchestration intelligente de services par un LLM

> 🇬🇧 English version: [README.en.md](README.en.md)

**Projet 6 — Module Systèmes Répartis et Distribués (Master ADIA, 2026)**

Assistant intelligent de gestion du catalogue d'un site e-commerce d'électronique et d'accessoires.
Le LLM analyse une demande en langage naturel et choisit les services à appeler ; l'orchestrateur
contrôle et exécute ces actions. Aucune décision n'est enregistrée sans validation humaine.

> 🚧 Projet en cours de développement.

## Structure du dépôt

```
orchestrator/        Orchestrateur (FastAPI)
services/
├── sales/           Service ventes
├── inventory/       Service stocks
├── catalog/         Service catalogue
└── analysis/        Service analyse (worker RabbitMQ)
data/                Générateur de données fictives
frontend/            Interface web
tests/               Tests et scénarios de panne
docs/                Rapport, présentation, diagrammes
docker-compose.yml   Déploiement
```

## Équipe

| Membre | GitHub | Responsabilité |
|---|---|---|
| Doha Mounazil | [@Doha-Mnzl](https://github.com/Doha-Mnzl) | _À définir_ |
| Manal Farouqi | [@manalfarouq](https://github.com/manalfarouq) | _À définir_ |
| Zakaria Maghouri | _À venir_ | _À définir_ |

Méthode de travail : voir [CONTRIBUTING.md](CONTRIBUTING.md).
