"""Page Décisions : propositions en attente (orchestrateur) et historique audité (catalogue)."""

import pandas as pd
import streamlit as st

from frontend import formatting as fmt
from frontend.client import ApiError, orchestrator, services
from frontend.config import settings
from frontend.views.components import decision_card, orchestrator_simulated, reconcile_with_queue


def render() -> None:
    st.title("Décisions")
    st.subheader("Propositions en attente")
    try:
        pending = orchestrator.pending_actions()
    except ApiError as e:
        st.error(f"Impossible de charger les propositions : {e.detail}.")
        pending = None
    if pending is not None:  # la file fait foi : une proposition revenue est déverrouillée
        reconcile_with_queue({a["action_id"] for a in pending})
    if pending == []:
        st.info("Aucune proposition en attente. Demandez une analyse à l'assistant pour en obtenir.")
    for action in pending or []:
        decision_card(action, key=f"dec_{action['action_id']}")
    # Actions traitées pendant la session et sorties de la file : leur résultat reste visible
    # (succès, refus, échec ou résultat inconnu), au lieu de disparaître sans retour.
    pending_ids = {a["action_id"] for a in pending or []}
    handled = [r["action"] for aid, r in st.session_state.get("decision_results", {}).items()
               if aid not in pending_ids and r.get("action")]
    if handled:
        st.markdown("**Traitées pendant cette session**")
        for action in handled:
            decision_card(action, key=f"dec_{action['action_id']}")

    st.subheader("Historique")
    mode = orchestrator_simulated()
    if mode is not False:  # simulé OU inconnu : jamais de données réelles à côté de résultats possiblement fictifs
        st.warning("Mode simulé : l'historique réel du catalogue n'est pas affiché à côté de résultats fictifs."
                   if mode else "Mode de l'orchestrateur inconnu (réel ou simulé) : l'historique réel du catalogue "
                   "n'est pas affiché.", icon="🧪")
        return
    if not settings.direct_read:
        st.info("Historique non disponible : lecture directe du catalogue désactivée.")
        return
    status = st.segmented_control("Statut", ["approved", "refused", "pending", "cancelled"],
                                  format_func=fmt.DECISION.get, key="hist_status")
    try:
        data = services.get("catalog", "/decisions", status=status, limit=200)
    except ApiError as e:
        st.error(f"Service catalogue indisponible : {e.detail}.")
        return
    if not data["items"]:
        st.caption("Aucune décision pour ce filtre.")
        return
    st.dataframe(pd.DataFrame([{
        "N°": d["decision_id"], "Créée le": d["created_at"][:16].replace("T", " "),
        "Action": fmt.ACTION.get(d["action"], d["action"]), "Produit": d["product_id"],
        "Remplaçant": d["replacement_id"] or "",
        "Statut": fmt.DECISION.get(d["decision_status"], d["decision_status"]),
        "Décideur (déclaré)": d["decided_by"] or "", "Enregistrée par (client authentifié)": d.get("recorded_by_client", ""),
        "Motif": d["reason"]} for d in data["items"]]), hide_index=True, width="stretch")
    st.caption("Le décideur est déclaré par le client ; seul le client de service est authentifié (jeton).")
    choice = st.selectbox("Journal d'audit d'une décision", [d["decision_id"] for d in data["items"]],
                          format_func=lambda i: f"Décision n° {i}", index=None, placeholder="Choisir une décision")
    if choice:
        try:
            detail = services.get("catalog", f"/decisions/{choice}")
        except ApiError as e:
            st.error(e.detail)
            return
        st.dataframe(pd.DataFrame([{"Date": e["at"][:19].replace("T", " "),
                                    "De": fmt.DECISION.get(e["from_status"], "création"),
                                    "Vers": fmt.DECISION.get(e["to_status"], e["to_status"]),
                                    "Par": e["actor"], "Note": e["note"] or ""} for e in detail["events"]]),
                     hide_index=True, width="stretch")
