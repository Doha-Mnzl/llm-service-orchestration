"""Interface Streamlit de la Partie 3 (port 8501).

Lancer depuis le dossier frontend/ (pour appliquer le thème .streamlit/config.toml) :
    cd frontend && streamlit run app.py --server.port 8501
ou depuis la racine : streamlit run frontend/app.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from frontend.config import settings  # noqa: E402
from frontend.views import assistant, dashboard, decisions, status  # noqa: E402

st.set_page_config(page_title="SmartRetail — assistant", page_icon="🛒", layout="wide")

PAGES = {"Assistant": assistant, "Tableau de bord": dashboard, "Décisions": decisions, "État du système": status}

with st.sidebar:
    st.markdown(f"### {settings.store_name}")
    page = st.radio("Navigation", list(PAGES), key="page", label_visibility="collapsed")
    st.divider()
    st.caption("Le chat passe par l'orchestrateur, qui contrôle chaque appel. "
               "Aucune décision n'est enregistrée sans validation humaine.")
    if page == "Assistant" and st.session_state.get("messages"):
        if st.button("Nouvelle conversation"):
            st.session_state["messages"] = []
            st.rerun()

PAGES[page].render()
