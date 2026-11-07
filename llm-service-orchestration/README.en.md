# Intelligent Service Orchestration Using an LLM

> 🇫🇷 Version française (principale) : [README.md](README.md)

**Project 6 — Distributed Systems module (Master's in Data Analytics & AI, 2026)**

An intelligent catalog-management assistant for an electronics and accessories e-commerce store.
The LLM interprets a natural-language request and chooses which services to call; the orchestrator
controls and executes those actions. No decision is saved without human approval.

> 🚧 Work in progress.

## Repository layout

```
orchestrator/        Orchestrator (FastAPI)
services/
├── sales/           Sales service
├── inventory/       Inventory service
├── catalog/         Catalog service
└── analysis/        Analysis service (RabbitMQ worker)
data/                Fake-data generator
frontend/            Web UI
tests/               Tests and failure scenarios
docs/                Report, presentation, diagrams
docker-compose.yml   Deployment
```

## Team

| Member | GitHub | Responsibility |
|---|---|---|
| Doha Mounazil | [@Doha-Mnzl](https://github.com/Doha-Mnzl) | _TBD_ |
| Manal Farouqi | [@manalfarouq](https://github.com/manalfarouq) | _TBD_ |
| Zakaria Maghouri | _TBD_ | _TBD_ |

Workflow: see [CONTRIBUTING.md](CONTRIBUTING.md).
