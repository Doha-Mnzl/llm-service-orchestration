# Décisions communes à valider (D-1 à D-6)

Aucune de ces décisions n'est validée par l'équipe. Le code de la Partie 3 applique une
**hypothèse configurable et réversible** pour chacune ; aucune ligne de code des Parties 1 et 2
n'a été modifiée.

| # | Question | Hypothèse appliquée | Réversibilité | À obtenir de |
|---|---|---|---|---|
| D-1 | Devise : DH (Jira LSO-25) ou € (`docs/api.md`) ? | DH (`CURRENCY=MAD`), champ `currency` dans les réponses ; prix du seed en DH | Variable d'environnement ; schéma sans devise | Doha, Zakaria |
| D-2 | Analysis lit-il PostgreSQL ou les API REST ? | REST (LSO-8 : pas d'accès direct à la base) ; route `/catalog/candidates/{id}` prévue | Si SQL direct : publier des vues en lecture seule documentées | Zakaria |
| D-3 | Cycle de vie et sécurité des décisions | Catalogue sûr par défaut : jeton de service, `decision` explicite, `decided_by` déclaré, `Idempotency-Key` stricte | Mode `DECISIONS_LEGACY_ORCHESTRATOR=true` pour l'orchestrateur actuel (non sûr, démonstration) | Doha |
| D-4 | Formules (`trend_6m`, marge, couverture, surstock) | Voir [api.md](api.md) ; fenêtre 30 j, surstock 180 j (valeur des mocks) | `DEMAND_WINDOW_DAYS`, `OVERSTOCK_DAYS` ; fonctions pures isolées | Zakaria |
| D-5 | Les tableaux de bord lisent-ils les services en GET direct ? | Oui, en lecture seule ; toute action passe par l'orchestrateur | `DASHBOARD_DIRECT_READ=false` coupe tout appel direct | Doha |
| D-6 | Ports, base, variables, Compose | 8001/8002/8003/8501 (défauts de `orchestrator/config.py`), une base, 3 schémas, `DATABASE_URL` | [compose-proposition.yml](compose-proposition.yml) | Zakaria |

## Intégration des décisions avec la Partie 2 (H5) — état réel et contrat cible

Le service catalogue est désormais **sûr par défaut** : une écriture de décision exige un jeton de
service et une décision explicite. L'orchestrateur actuel (PR #1, non modifié) n'envoie ni l'un ni
l'autre : en mode sûr, son appel `record_decision` reçoit **401** et rien n'est enregistré (testé).
Pour la démonstration, `DECISIONS_LEGACY_ORCHESTRATOR=true` accepte son appel en le traçant comme
client `legacy-unauthenticated` (source `legacy`) avec un décideur « inconnu » : ce mode n'est **pas**
sûr. Il est restreint à la forme exacte de l'appel actuel (aucun autre champ), ne permet aucune
transition, et déduplique les reprises identiques sur 60 s faute de clé.

| Problème constaté dans la Partie 2 (fichier, fonction) | Conséquence | Contrat cible demandé |
|---|---|---|
| `agent.decide()` : sur refus, rien n'est envoyé au catalogue | Refus absents de l'audit (LSO-11, LSO-23) | `POST /decisions` aussi pour un refus, avec `"decision": "refused"` |
| `DecisionRequest` ne contient que `approved` ; aucun auteur transmis | Décideur inconnu | Ajouter `decided_by` (rempli par l'interface), envoyé tel quel |
| Aucune clé d'idempotence ; `executor.execute()` rejoue un POST après un délai dépassé | Doublon possible si la première requête a abouti | En-tête `Idempotency-Key: <action_id>` (stable pour toutes les reprises) — **obligatoire** côté catalogue (`400` sinon) |
| Aucun jeton de service | Écriture impossible en mode sûr | En-tête `Authorization: Bearer ${CATALOG_TOKEN}` |
| `PENDING` en mémoire | Propositions perdues au redémarrage ; l'interface affiche « n'est plus en attente » | Option : créer la proposition dans le catalogue (`"decision": "pending"`) puis `/approve` ou `/refuse` |
| Corps sans `decision` (approbation implicite) | Refusé en mode sûr (422) | Toujours envoyer `"decision"` |

**Défaut reproduit par Codex (HIGH, Partie 2)** : `agent.decide()` retire l'action de `PENDING` avant
l'appel et ne la restaure que sur `ServiceUnavailable`. Un refus HTTP du catalogue (401 sans jeton, 400 sans
clé, 409, 422) remonte en `httpx.HTTPStatusError` : `POST /actions/{id}/decision` répond **500** et la
proposition **disparaît** (`GET /actions/pending` ⇒ `[]`). Test de régression prêt :
`tests/integration/test_orchestrator_compat.py::test_rejected_catalog_write_keeps_the_proposal_pending`
(`xfail` strict tant que la PR #1 n'est pas corrigée). Changement attendu : capturer `HTTPStatusError`,
**restaurer l'action** (ou lui donner un statut consultable), répondre `{"status": "failed", "detail": …,
"catalog_status": 401}` sans 500, et ne jamais perdre une proposition sur une erreur métier.

Requête attendue du catalogue, pour une validation et pour un refus :

```http
POST /decisions
Authorization: Bearer <CATALOG_TOKEN>
Idempotency-Key: <action_id>
Content-Type: application/json

{"product_id": "P005", "action": "replace", "replacement_id": "S104",
 "reason": "Ventes en forte baisse et stock élevé",
 "decision": "approved" | "refused", "decided_by": "<nom saisi dans l'interface>"}
```

Réponses : `201` créée ; `200` rejeu identique (même clé, mêmes champs) ; `409` même clé avec des
champs différents ; `401` jeton absent ou invalide ; `404` produit inconnu ; `422` données invalides
ou référence fournisseur inexistante ; `500`/`503` rien n'a été validé (reprise sûre avec la même clé).

Changement minimal proposé dans `orchestrator/agent.py` (à réaliser par Doha, **non appliqué**) :
dans `decide(action_id, approved, decided_by)`, appeler `execute()` dans les deux cas avec
`{**action.args, "decision": "approved" if approved else "refused", "decided_by": decided_by}`, et
faire transmettre par `executor` les en-têtes `Authorization` et `Idempotency-Key: action_id`
pour l'outil `record_decision` (`RecordDecisionArgs` interdit les champs supplémentaires : les
ajouter au modèle ou les passer hors validation LLM, puisqu'ils viennent de l'humain et non du LLM).

## Brouillon de revue pour la PR #1 (à publier par Manal si elle le souhaite)

> Merci Doha, le contrat fonctionne avec les vrais services de la Partie 3 : tes 5 outils HTTP de
> lecture passent avec ton exécuteur et les réponses contiennent tous les champs de tes mocks
> (`tests/integration/test_orchestrator_compat.py`). `record_decision` ne passe qu'en mode de
> compatibilité tant que les points 3 ci-dessous ne sont pas faits. Points à trancher avant de figer
> `docs/api.md` :
>
> 1. **Devise** : Jira (LSO-25) demande des DH ; je propose `MAD` + un champ `currency` dans les
>    réponses, et d'adapter le texte « € » des mocks.
> 2. **`quantity` dans `/stocks`** = stock disponible (physique − réservé) ; j'ajoute `on_hand`,
>    `reserved`, `available`, `coverage_status`.
> 3. **Décisions** : le catalogue exige maintenant un jeton de service (`Authorization: Bearer`), une
>    `decision` explicite et accepte `decided_by` et `Idempotency-Key`. Pourrais-tu les envoyer depuis
>    `decide()`, y compris pour les refus (contrat exact dans `docs/partie3/decisions-equipe.md`) ?
>    En attendant, un mode de compatibilité non sûr existe pour la démo.
> 4. **Analysis** (avec Zakaria) : lecture par REST plutôt que SQL direct ; formules de `trend_6m` et
>    de couverture à valider (voir `docs/partie3/api.md`).
> 5. `pytest.ini` : ma branche ajoute exactement le même fichier ; la fusion des deux branches a été
>    simulée, tous les tests passent ensemble.
