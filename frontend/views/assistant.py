"""Page Assistant : chat connecté EXCLUSIVEMENT à l'orchestrateur (LSO-12, LSO-24, LSO-13)."""

import pandas as pd
import streamlit as st

from frontend import formatting as fmt
from frontend.client import ApiError, Unavailable, orchestrator
from frontend.views.components import decision_card, orchestrator_simulated

EXAMPLES = ["Quels produits dois-je retirer ce mois-ci, et par quoi les remplacer ?",
            "Quels produits sont en rupture ou en surstock ?",
            "Quelles sont les ventes et la marge du mois ?",
            "Cherche des câbles dans le catalogue fournisseurs"]

STATUS_BANNER = {
    "partial": ("warning", "Réponse partielle : un service non essentiel n'a pas répondu."),
    "stopped": ("error", "Analyse arrêtée par l'orchestrateur."),
    "error": ("error", "L'analyse n'a pas pu être réalisée."),
}


def _trace_table(trace: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([{"Étape": i + 1, "Outil": fmt.TOOLS.get(t["tool"], t["tool"]),
                          "Statut": fmt.TRACE.get(t["status"], t["status"]), "Tentatives": t.get("attempts", 0),
                          "Durée (ms)": t.get("duration_ms", 0), "Détail": t.get("detail") or ""}
                         for i, t in enumerate(trace)])


def _render_assistant(msg: dict, idx: int) -> None:
    if msg.get("error"):
        st.error(f"{msg['error']}. Aucune analyse n'a été faite et aucun chiffre n'est affiché.")
        return
    resp = msg["response"]
    kind, text = STATUS_BANNER.get(resp.get("status"), (None, None))
    if kind:
        getattr(st, kind)(text + ("  \n" + "  \n".join(resp.get("warnings", [])) if resp.get("warnings") else ""))
    st.markdown(resp.get("answer") or "_Réponse vide._")
    trace = resp.get("trace") or []
    with st.expander(f"Appels aux services ({len(trace)})", expanded=resp.get("status") != "ok"):
        if trace:
            st.dataframe(_trace_table(trace), hide_index=True, width="stretch")
        else:
            st.caption("Aucun service appelé.")
        st.caption(f"Moteur LLM : {resp.get('llm', '?')}, statut : {resp.get('status')}")
    for action in resp.get("pending_actions") or []:
        decision_card(action, key=f"{idx}_{action['action_id']}")


def render() -> None:
    st.title("Assistant commercial")
    st.caption("Posez une question en langage naturel. L'orchestrateur choisit les services à interroger ; "
               "aucune décision n'est enregistrée sans votre validation.")
    if orchestrator_simulated():
        st.warning("Mode simulé : l'orchestrateur utilise ses services fictifs. Les chiffres, stocks et montants "
                   "des réponses sont fictifs, et aucune décision n'est écrite dans le catalogue réel.", icon="🧪")
    messages = st.session_state.setdefault("messages", [])

    typed = st.chat_input("Votre question sur les ventes, les stocks ou le catalogue")
    clicked = None
    if not messages and not typed:
        cols = st.columns(2)
        for i, q in enumerate(EXAMPLES):
            if cols[i % 2].button(q, key=f"example_{i}", width="stretch"):
                clicked = q

    for idx, msg in enumerate(messages):
        with st.chat_message(msg["role"]):
            if msg["role"] == "user":
                st.markdown(msg["content"])
            else:
                _render_assistant(msg, idx)

    prompt = typed or clicked
    if not prompt:
        return
    messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant"):
        with st.status("Analyse en cours : l'orchestrateur interroge les services…") as status:
            try:
                msg = {"role": "assistant", "response": orchestrator.chat(prompt)}
                if msg["response"].get("status") in ("ok", "partial"):
                    status.update(label="Analyse terminée", state="complete")
                else:
                    status.update(label="Analyse interrompue", state="error")
            except Unavailable as e:
                msg = {"role": "assistant", "error": f"L'orchestrateur ne répond pas ({e.detail})"}
                status.update(label="Orchestrateur indisponible", state="error")
            except ApiError as e:
                msg = {"role": "assistant", "error": f"Requête refusée par l'orchestrateur (HTTP {e.status} : "
                                                     f"{e.detail})"}
                status.update(label="Requête refusée", state="error")
        messages.append(msg)
        _render_assistant(msg, len(messages) - 1)
