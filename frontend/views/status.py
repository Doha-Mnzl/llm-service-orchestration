"""Page État du système : orchestrateur et services (LSO-13)."""

from datetime import datetime

import pandas as pd
import streamlit as st

from frontend.client import ApiError, orchestrator, services
from frontend.config import settings

LABEL = {"up": "✅ Disponible", "down": "⛔ Indisponible"}


def _label(state: str) -> str:
    if state.startswith("up"):
        return "✅ Disponible" + (" (simulé)" if "mock" in state else "")
    return LABEL.get(state, f"⚠️ {state}")


def render() -> None:
    st.title("État du système")
    if st.button("Actualiser"):
        st.rerun()
    st.caption(f"Vérifié à {datetime.now():%H:%M:%S}")

    st.subheader("Orchestrateur")
    try:
        h = orchestrator.health()
        c1, c2, c3 = st.columns(3)
        c1.metric("Orchestrateur", "✅ Disponible")
        c2.metric("Moteur LLM", h.get("llm", "?"))
        mode = h.get("mock_services")  # seul un booléen explicite fait foi
        c3.metric("Services", {True: "simulés", False: "réels"}.get(mode, "inconnu") if isinstance(mode, bool)
                  else "inconnu")
        seen = orchestrator.services_health()
        st.dataframe(pd.DataFrame([{"Service": k, "État vu par l'orchestrateur": _label(v)}
                                   for k, v in seen.items()]), hide_index=True, width="stretch")
    except ApiError as e:
        st.error(f"{e.detail}. Le chat et les validations sont indisponibles tant qu'il ne répond pas.")

    st.subheader("Services de données (Partie 3)")
    if not settings.direct_read:
        st.info("Contrôle direct désactivé (DASHBOARD_DIRECT_READ=false).")
        return
    rows, legacy = [], False
    for name, url in settings.service_urls.items():
        state, ms, payload = services.health(name)
        legacy = legacy or (name == "catalog" and payload.get("legacy_mode") is True)
        rows.append({"Service": services.LABELS[name], "Adresse": url, "État": _label(state),
                     "Temps de réponse (ms)": ms})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    if legacy:
        st.warning("Catalogue en mode de compatibilité NON SÛR (DECISIONS_LEGACY_ORCHESTRATOR=true) : "
                   "il accepte des décisions non authentifiées. À réserver à la démonstration.")
