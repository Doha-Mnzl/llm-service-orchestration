# Interface

Interface web : chat, suivi des appels, validation des décisions et état du système.

*Web UI: chat, call trace, decision approval and system status.*

## Lancer et tester

```bash
cd frontend && ORCHESTRATOR_URL=http://localhost:8000 streamlit run app.py   # thème : .streamlit/config.toml
pytest tests/frontend        # depuis la racine : AppTest + faux backend HTTP
```

| Page | Source des données |
|---|---|
| Assistant (chat, trace des appels, Valider / Refuser avec confirmation) | Orchestrateur uniquement |
| Tableau de bord (KPI, évolution mensuelle, produits à surveiller, alertes, recommandations) | Services en lecture seule (GET) + orchestrateur |
| Décisions (propositions en attente, historique audité) | Orchestrateur + catalogue (GET) |
| État du système | Orchestrateur + `/health` des services |

`client.py` est le seul point de sortie réseau : le client des services n'expose que des GET, et
aucune URL de fournisseur LLM n'existe dans le frontend (vérifié par les tests).
`DASHBOARD_DIRECT_READ=false` coupe tout appel direct aux services.
