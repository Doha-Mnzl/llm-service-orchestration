"""Page Tableau de bord : ventes, marges, stocks et recommandations (LSO-25).

Chaque bloc affiche UNIQUEMENT ce que renvoient les API ; si un service est indisponible,
le bloc concerné affiche l'erreur et les autres continuent de fonctionner.
"""

import altair as alt
import pandas as pd
import streamlit as st

from frontend import formatting as fmt
from frontend.client import ApiError, orchestrator, services
from frontend.config import settings
from frontend.views.components import orchestrator_simulated

ACCENT, INK_MUTED, GRID = "#3A5FA8", "#5B6B73", "#E3E8EC"
PERIODS = {1: "1 mois", 3: "3 mois", 6: "6 mois", 12: "12 mois"}
MEASURES = {"revenue": "Chiffre d'affaires", "gross_margin": "Marge brute", "units": "Unités vendues"}


def _fetch(service: str, path: str, **params):
    try:
        return services.get(service, path, **params), None
    except ApiError as e:
        return None, e.detail


def has_sales(items: list[dict]) -> bool:
    return sum(i.get("units", 0) or 0 for i in items) > 0


def _filters():
    cats, err = _fetch("catalog", "/catalog/categories")
    c1, c2, c3 = st.columns([3, 2, 3])
    period = c1.segmented_control("Période", list(PERIODS), format_func=PERIODS.get, default=1,
                                  key="dash_period") or 1
    options = [None] + [c["name"] for c in cats or []]
    category = c2.selectbox("Catégorie", options, key="dash_category",
                            format_func=lambda c: "Toutes les catégories" if c is None else c)
    if err:
        c2.caption(f"Catégories indisponibles ({err})")
    return period, category, c3


def _kpis(summary: dict) -> None:
    items, cur = summary["items"], summary["currency"]
    revenue = sum(i["revenue"] for i in items)
    previous = sum(i["previous_revenue"] for i in items)
    margin = sum(i["gross_margin"] for i in items)
    units = sum(i["units_sold"] for i in items)
    change = (revenue - previous) / previous if previous else None
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Chiffre d'affaires", fmt.money(revenue, cur, 0),
              delta=fmt.percent(change, signed=True) if change is not None else None,
              help="Évolution par rapport à la période précédente de même durée")
    k2.metric("Marge brute", fmt.money(margin, cur, 0))
    k3.metric("Taux de marge", fmt.percent(margin / revenue) if revenue else "—")
    k4.metric("Unités vendues", fmt.number(units))
    st.caption(f"Du {summary['start']} au {summary['end']}, montants en {fmt.currency_label(cur)}.")


def _monthly_chart(category, product_id) -> None:
    measure = st.segmented_control("Indicateur", list(MEASURES), format_func=MEASURES.get, default="revenue",
                                   key="dash_measure") or "revenue"
    data, err = _fetch("sales", "/sales/monthly", months=12, category=category, product_id=product_id)
    if err:
        st.error(f"Évolution mensuelle indisponible : {err}")
        return
    df = pd.DataFrame(data["items"])
    if df.empty or not has_sales(data["items"]):  # absence de ventes = aucune unité, pas une marge nulle
        st.info("Aucune vente sur les 12 derniers mois pour ce filtre.")
        return
    unit = "unités" if measure == "units" else fmt.currency_label(data["currency"])
    df["valeur"] = df[measure].map(lambda v: f"{fmt.number(v, 0 if measure == 'units' else 2)} {unit}")
    chart = (alt.Chart(df, title=alt.Title(f"{MEASURES[measure]} par mois", anchor="start", fontWeight=600))
             .mark_bar(color=ACCENT, cornerRadiusTopLeft=4, cornerRadiusTopRight=4, width={"band": 0.8})
             .encode(x=alt.X("month:O", title=None, axis=alt.Axis(labelAngle=0, labelColor=INK_MUTED)),
                     y=alt.Y(f"{measure}:Q", title=unit, axis=alt.Axis(gridColor=GRID, labelColor=INK_MUTED, domain=False, ticks=False,
                                                    labelExpr="replace(format(datum.value, ',.0f'), /,/g, ' ')")),
                     tooltip=[alt.Tooltip("month:O", title="Mois"), alt.Tooltip("valeur:N", title=MEASURES[measure])])
             .properties(height=280).configure_view(stroke=None))
    st.altair_chart(chart, width="stretch")
    with st.expander("Voir les données du graphique"):
        st.dataframe(df[["month", "units", "revenue", "gross_margin"]].rename(columns={
            "month": "Mois", "units": "Unités", "revenue": "CA", "gross_margin": "Marge brute"}),
            hide_index=True, width="stretch")


def _watchlist(summary: dict, category) -> None:
    stocks, err = _fetch("inventory", "/stocks", category=category)
    by_id = {s["product_id"]: s for s in (stocks or {}).get("items", [])}
    rows = []
    for i in sorted(summary["items"], key=lambda i: (i["trend_6m"] is None, i["trend_6m"] or 0)):
        s = by_id.get(i["product_id"], {})
        rows.append({"Produit": f"{i['product_id']} — {i['name']}", "Catégorie": i["category"],
                     "CA": i["revenue"], "Taux de marge": i["margin_rate"],
                     "Tendance 6 mois": i["trend_6m"], "Stock dispo": s.get("available"),
                     "Couverture (j)": s.get("coverage_days"),
                     "État du stock": fmt.COVERAGE.get(s.get("coverage_status"), "—") if s else "—"})
    st.subheader("Produits à surveiller")
    st.caption("Triés par tendance des ventes (3 derniers mois comparés aux 3 précédents), la plus forte baisse "
               "en premier.")
    if err:
        st.warning(f"Données de stock indisponibles ({err}) : seules les ventes sont affichées.")
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=360, column_config={
        "CA": st.column_config.NumberColumn(format=f"%.2f {fmt.currency_label(summary['currency'])}"),
        "Taux de marge": st.column_config.NumberColumn(format="percent"),
        "Tendance 6 mois": st.column_config.NumberColumn(format="percent"),
    })


def _stock_alerts() -> None:
    st.subheader("Alertes de stock")
    alerts, err = _fetch("inventory", "/stocks/alerts", kind="all")
    if err:
        st.error(f"Service stocks indisponible : {err}")
        return
    c1, c2, c3 = st.columns(3)
    c1.metric("⛔ Ruptures", len(alerts["stockout"]))
    c2.metric("⚠️ Stocks critiques", len(alerts["critical"]))
    c3.metric("📦 Surstocks et sans demande", len(alerts["overstock"]))
    t1, t2, t3 = st.tabs(["Ruptures", "Critiques", "Surstocks"])
    t1.dataframe(pd.DataFrame([{"Produit": f"{a['product_id']} — {a['name']}", "Depuis le": a["since"],
                                "Jours de rupture": a["days_out"]} for a in alerts["stockout"]]),
                 hide_index=True, width="stretch")
    t2.dataframe(pd.DataFrame([{"Produit": f"{a['product_id']} — {a['name']}", "Disponible": a["quantity"],
                                "Seuil d'alerte": a["reorder_point"], "Couverture (j)": a["coverage_days"]}
                               for a in alerts["critical"]]), hide_index=True, width="stretch")
    t3.dataframe(pd.DataFrame([{"Produit": f"{a['product_id']} — {a['name']}", "Disponible": a["quantity"],
                                "Couverture (j)": a["coverage_days"] if a["coverage_days"] is not None
                                else "sans demande", "État": fmt.COVERAGE.get(a["coverage_status"], a["coverage_status"])}
                               for a in alerts["overstock"]]), hide_index=True, width="stretch")


def _recommendations() -> None:
    st.subheader("Recommandations")
    left, right = st.columns(2)
    try:
        pending = orchestrator.pending_actions()
        left.metric("Propositions en attente de validation", len(pending))
        left.caption("À traiter dans la page Décisions.")
    except ApiError as e:
        left.error(f"Propositions indisponibles : {e.detail}")
    data, err = _fetch("catalog", "/decisions", limit=5)
    with right:
        st.markdown("**Dernières décisions**")
        if err:
            st.error(f"Historique indisponible : {err}")
        elif not data["items"]:
            st.caption("Aucune décision enregistrée.")
        else:
            for d in data["items"]:
                rep = f" par {d['replacement_id']}" if d["replacement_id"] else ""
                st.markdown(f"{fmt.DECISION.get(d['decision_status'], d['decision_status'])} : {fmt.ACTION.get(d['action'], d['action'])} "
                            f"{d['product_id']}{rep}")


def render() -> None:
    st.title("Tableau de bord")
    mode = orchestrator_simulated()
    if mode is not False:  # simulé OU inconnu
        st.warning("Mode simulé : l'interface est reliée à un orchestrateur fictif. Les indicateurs réels ne sont "
                   "pas affichés ici pour ne pas les mélanger avec des résultats simulés." if mode else
                   "Mode de l'orchestrateur inconnu (réel ou simulé) : les indicateurs réels ne sont pas affichés "
                   "tant qu'il ne confirme pas son mode.", icon="🧪")
        return
    if not settings.direct_read:
        st.info("La lecture directe des services est désactivée (DASHBOARD_DIRECT_READ=false). "
                "Utilisez l'assistant pour consulter les indicateurs.")
        return
    period, category, product_slot = _filters()
    summary, err = _fetch("sales", "/sales/summary", period_months=period, category=category)
    if err:
        st.error(f"Service ventes indisponible : {err}. Aucun indicateur de vente n'est affiché.")
    else:
        st.session_state["currency"] = summary["currency"]
        products = {i["product_id"]: f"{i['product_id']} — {i['name']}" for i in summary["items"]}
        product_id = product_slot.selectbox("Produit", [None, *products], key="dash_product",
                                            format_func=lambda p: "Tous les produits" if p is None else products[p])
        if product_id:
            summary = {**summary, "items": [i for i in summary["items"] if i["product_id"] == product_id]}
        if not summary["items"]:
            st.info("Aucun produit ne correspond à ce filtre.")
        else:
            _kpis(summary)
            _monthly_chart(category, product_id)
            _watchlist(summary, category)
    st.divider()
    _stock_alerts()
    st.divider()
    _recommendations()
