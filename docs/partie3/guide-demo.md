# Comprendre et tester la Partie 3

Guide pour comprendre ce que fait la Partie 3 et la tester soi-même sur une pile **isolée** (base
`lso_demo_test`, jamais `lso_dev`). L’orchestrateur de référence est la révision externe `06e5b51` de la PR #1, fermée sans fusion.

## 1. Les composants et leur rôle

| Composant | Rôle | Où le voir |
|---|---|---|
| PostgreSQL (3 schémas) | `sales` (ventes), `inventory` (stocks, mouvements, ruptures), `catalog` (produits, fournisseurs, décisions + audit). Chaque service ne lit que **son** schéma | — |
| Générateur `data.generate` | Crée 18 mois de ventes, des stocks simulés jour par jour, 200 références fournisseurs ; même graine ⇒ mêmes données | empreinte affichée par `data.verify` |
| Service ventes (FastAPI) | CA, marge, tendance, évolution par période | tableau de bord |
| Service stocks | Physique, réservé, disponible, couverture, alertes, ruptures | onglets d'alertes |
| Service catalogue | Produits, fournisseurs, **décisions humaines** sécurisées et auditées | page Décisions |
| Interface Streamlit | Chat, tableau de bord, décisions, état du système. Elle **n'écrit jamais** directement dans les services | navigation latérale |
| Orchestrateur (Doha, PR #1, non modifié) | Reçoit la question, laisse le LLM choisir les outils, contrôle et appelle les services | trace des appels |

## 2. Le trajet d'une question

Exemple visible : « Quels produits sont en rupture ou en surstock ? ».

1. **Streamlit** envoie la phrase à l'orchestrateur : `POST /chat` (fichier `frontend/client.py`).
2. **L'orchestrateur** demande au LLM (ici le faux LLM de démonstration) quel outil utiliser ; il
   vérifie que l'outil est autorisé et que ses arguments sont valides.
3. Il appelle **mon service stocks** : `GET /stocks/alerts`.
4. Le service stocks exécute une **requête SQL paramétrée** sur le schéma `inventory` et calcule la
   couverture de chaque produit.
5. La réponse remonte : l'orchestrateur rédige la réponse ; Streamlit l'affiche avec la **trace** :
   outil « Alertes de stock », statut « Réussi », 1 tentative, durée en ms.

Si l'orchestrateur ne répond pas, l'interface le dit et n'affiche aucun chiffre. Si un outil dépend d'un composant absent
(Analysis, Partie 1), l'analyse s'arrête proprement.

## 3. Comment les indicateurs sont calculés (valeurs réelles de la démo)

Produit **P003 « Souris filaire basique »**, octobre 2026 (`GET /sales/summary?product_id=P003`) :

| Indicateur | Formule | Valeur |
|---|---|---|
| Unités vendues | Σ quantités du mois | 18 |
| Chiffre d'affaires | Σ quantité × prix unitaire | 1 260,00 DH |
| Marge brute | Σ quantité × (prix − coût) | 151,20 DH |
| Taux de marge | marge / CA | 12 % |
| Tendance 6 mois | (unités des 3 derniers mois − 3 précédents) / 3 précédents | −50,7 % |
| Évolution du CA | (CA du mois − CA du mois précédent) / CA précédent | −61,7 % |

Produit **P005 « Écouteurs filaires jack »** (`GET /stocks/P005`) : physique 418 − réservé 14 =
**disponible 404** ; demande moyenne = sorties des 30 derniers jours / 30 = **0,8 par jour** ;
couverture = 404 / 0,8 = **505 jours** > 180 ⇒ statut **surstock**. Demande nulle ⇒ couverture « non
définie » ; stock nul ⇒ « rupture ».

## 4. La validation humaine, étape par étape

1. Une proposition (« Remplacer P005 par S104 ») vient de l'orchestrateur ; elle reste **en attente**.
2. Dans l'interface : **Valider** ou **Refuser**, puis **Confirmer**.
3. L'interface transmet la décision à l'orchestrateur (`POST /actions/{id}/decision`), jamais au catalogue.
4. Le catalogue n'enregistre une décision que si l'appelant présente un **jeton de service**, une
   **décision explicite** et une **clé d'idempotence**. La décision et son événement d'audit sont écrits
   dans **une seule transaction**, validée avant la réponse.
5. Une reprise réseau avec la même clé ne crée pas de doublon (réponse 200 « rejeu »). C'est aussi pour
   cela que les numéros peuvent sauter :
   PostgreSQL consomme un numéro même quand le rejeu n'insère rien. C'est normal.
6. Si la réponse se perd (panne, délai), l'interface affiche « **Résultat inconnu** » et propose
   « **Vérifier l'état** » avant toute nouvelle tentative : jamais de faux succès.
7. L'historique montre le décideur **déclaré** et le client **authentifié** (`demo`, `seed`) ; le journal
   d'audit montre chaque transition.

**Simulé ou réel ?** Sur l'interface reliée à l'orchestrateur mock (port 28502), un bandeau « Mode simulé »
est affiché, aucune donnée réelle n'est mélangée aux propositions fictives, et la validation est annoncée comme
**non écrite**. Les décisions réellement
persistées de la démo ont été enregistrées par le
**contrat cible** : client authentifié `demo`, jeton, clé — exactement ce que l'orchestrateur devra faire.

## 5. Ce qui fonctionne réellement, ce qui dépend des collègues

| Fonctionne (testé) | Dépend de Doha (Partie 2) | Dépend de Zakaria (Partie 1) |
|---|---|---|
| Données, 3 services, calculs, alertes | Envoyer jeton + `decision` + `decided_by` + `Idempotency-Key` (sinon 401/400 en mode sûr) | Worker Analysis + RabbitMQ (scénario « retirer / remplacer ») |
| Décisions sécurisées, idempotentes, auditées | Transmettre les **refus** au catalogue | `docker-compose.yml` commun (proposition fournie) |
| Interface : chat, trace, tableau de bord, décisions, état | Persister les propositions en attente | Volume PostgreSQL et tests de charge |
| Lecture par l'orchestrateur de mes 5 outils | — | — |

## 6. Tester soi-même

### Démarrer et arrêter la pile isolée

```bash
cd llm-service-orchestration && source .venv/bin/activate
git worktree add --detach ../orch 06e5b51          # une fois : copie de lecture de la PR #1
python scripts/partie3_demo.py start --orchestrator-src ../orch
python scripts/partie3_demo.py status
python scripts/partie3_demo.py stop-service inventory           # panne simulée
python scripts/partie3_demo.py start-service inventory          # rétablissement
python scripts/partie3_demo.py stop                             # arrête tout ; la base est conservée
```

Le lanceur refuse toute base sans « test » dans son nom, écoute uniquement sur 127.0.0.1, démarre le
catalogue en **mode sûr** et écrit le jeton de démonstration dans `$XDG_RUNTIME_DIR/lso-partie3-demo/28000/`
(droits 600).

### Adresses

| Application | URL |
|---|---|
| Interface (orchestrateur réel, vrais services) | http://127.0.0.1:28501 |
| Interface (orchestrateur simulé) | http://127.0.0.1:28502 |
| API ventes / stocks / catalogue (documentation) | http://127.0.0.1:28001/docs · :28002/docs · :28003/docs |
| Orchestrateur réel / simulé | http://127.0.0.1:28010/docs · :28011/docs |

### À essayer et résultat attendu

| Où | Action | Résultat attendu |
|---|---|---|
| 28501 Assistant | « Quels produits sont en rupture ou en surstock ? » | Réponse + trace « Alertes de stock — Réussi » |
| 28501 Assistant | « Quelles sont les ventes et la marge du mois ? » | Trace « Synthèse des ventes — Réussi » |
| 28501 Assistant | « Quels produits dois-je retirer ce mois-ci ? » | « Analyse arrêtée » : Analysis (Partie 1) absent |
| 28501 Tableau de bord | 3 mois, catégorie « câbles », indicateur « Marge brute » | KPI en DH, graphique mensuel, produits à surveiller |
| 28501 Décisions | Filtre « En attente » | « Aucune décision pour ce filtre » (état vide) |
| 28501 Décisions | Choisir une décision dans « Journal d'audit » | Transitions, décideur déclaré |
| 28501 État du système | `stop-service inventory` puis « Actualiser » | Service stocks « Indisponible » ; le tableau de bord garde les ventes |
| 28502 Assistant | « Quels produits dois-je retirer… » puis Valider → Confirmer | Bandeau « Mode simulé », « rien n'a été écrit dans le catalogue réel » |
| 28502 Décisions | Nouvelle proposition → Refuser → Confirmer | Le résultat reste visible sous « Traitées pendant cette session » |
| Terminal | `curl -X POST 127.0.0.1:28003/decisions -H 'Content-Type: application/json' -d '{}'` | `401` : écriture refusée sans jeton |

Enregistrer une vraie décision (contrat cible, base `lso_demo_test`) :

```bash
TOKEN=$(cat $XDG_RUNTIME_DIR/lso-partie3-demo/28000/catalog_token)
curl -s -X POST http://127.0.0.1:28003/decisions -H "Authorization: Bearer $TOKEN" \
  -H "Idempotency-Key: essai-1" -H 'Content-Type: application/json' \
  -d '{"product_id":"P042","action":"keep","reason":"Essai manuel","decision":"approved","decided_by":"manal"}'
# relancer la même commande ⇒ 200 « idempotent_replay » ; changer "reason" avec la même clé ⇒ 409
```

### Limites de la démonstration

- Le faux LLM de l'orchestrateur répond avec des phrases simples (« get_stock_alerts : 10 élément(s)… ») ;
  un vrai LLM (Groq) est hors de la Partie 3.
- Le scénario de remplacement réel attend Analysis ; les propositions viennent donc de l'orchestrateur
  simulé, et leurs textes contiennent des « € » (mocks de la PR #1, décision D-1).
- Valider une proposition sur l'interface réelle n'est pas possible tant que l'orchestrateur n'envoie
  pas le jeton (401 en mode sûr) : c'est voulu.
- Les services de démonstration historiques (ports 8001–8003, base `lso_dev`) tournent encore avec
  l'ancien code ; avant de les redémarrer, appliquer `python -m data.migrate` sur `lso_dev`.
