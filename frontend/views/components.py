"""Composants partagés : carte de proposition (Valider / Refuser avec confirmation)."""

import streamlit as st

from frontend import formatting as fmt
from frontend.client import ApiError, Unavailable, orchestrator, services
from frontend.config import settings


def _safe_get(service: str, path: str):
    if not settings.direct_read:
        return None
    try:
        return services.get(service, path)
    except ApiError:
        return None


def _describe_product(pid: str) -> tuple[str, str]:
    p = _safe_get("catalog", f"/catalog/products/{pid}")
    s = _safe_get("inventory", f"/stocks/{pid}")
    title = f"{pid} — {p['name']}" if p else pid
    details = []
    if p:
        details.append(f"{p['category']}, {fmt.money(p['unit_price'], settings_currency())}")
    if s:
        details.append(f"stock disponible {fmt.number(s['available'])} ({fmt.COVERAGE.get(s['coverage_status'], s['coverage_status'])})")
    return title, "  \n".join(details) or "Détails indisponibles"


def _describe_replacement(sid: str) -> tuple[str, str]:
    sp = _safe_get("catalog", f"/catalog/supplier-products/{sid}")
    if not sp:
        return sid, "Détails indisponibles"
    return (f"{sid} — {sp['name']}",
            f"{sp['supplier_name']}, {fmt.money(sp['price'], settings_currency())}  \n"
            f"{fmt.AVAILABILITY.get(sp['availability'], sp['availability'])}, livraison en {sp['lead_time_days']} j")


def settings_currency() -> str:
    return st.session_state.get("currency", "MAD")


def orchestrator_simulated(ttl: float = 15.0) -> bool | None:
    """True si l'orchestrateur annonce des services simulés (mocks) ; None si inconnu.
    Mis en cache quelques secondes dans la session pour éviter un appel à chaque interaction."""
    import time
    cached = st.session_state.get("_orchestrator_mode")
    if cached and time.monotonic() - cached[0] < ttl:
        return cached[1]
    value = read_orchestrator_mode()
    if value is not None:  # inconnu : jamais mis en cache, jamais traité comme « réel »
        st.session_state["_orchestrator_mode"] = (time.monotonic(), value)
    return value


def read_orchestrator_mode() -> bool | None:
    """Lecture directe de /health : seul un booléen explicite `mock_services` fait foi."""
    try:
        value = orchestrator.health().get("mock_services")
    except (ApiError, AttributeError):
        return None
    return value if isinstance(value, bool) else None


SIMULATED_DETAILS = "Détails réels non affichés en mode simulé (données fictives de l'orchestrateur)"
UNKNOWN_DETAILS = "Détails non affichés : mode de l'orchestrateur inconnu (réel ou simulé)"


def decision_card(action: dict, key: str) -> None:
    """Affiche une proposition en attente et ses boutons de décision (une seule décision possible)."""
    args, aid = action.get("args", {}), action["action_id"]
    results = st.session_state.setdefault("decision_results", {})
    with st.container(border=True):
        verb = fmt.ACTION.get(args.get("action"), args.get("action", "?"))
        st.markdown(f"**{verb}**, proposition `{aid}`")
        simulated = orchestrator_simulated()
        # Données réelles affichées SEULEMENT si l'orchestrateur confirme le mode réel (False) :
        # jamais à côté d'une proposition fictive (True) ni quand le mode est inconnu (None).
        placeholder = None if simulated is False else (SIMULATED_DETAILS if simulated else UNKNOWN_DETAILS)
        if placeholder:
            title, details = args.get("product_id", "?"), placeholder
        else:
            title, details = _describe_product(args.get("product_id", "?"))
        if args.get("action") == "replace" and args.get("replacement_id"):
            left, mid, right = st.columns([5, 1, 5], vertical_alignment="center")
            left.markdown(f"**{title}**  \n{details}")
            mid.markdown("<div style='text-align:center;font-size:1.6rem' aria-label='remplacé par'>→</div>",
                         unsafe_allow_html=True)
            r_title, r_details = (args["replacement_id"], placeholder) if placeholder \
                else _describe_replacement(args["replacement_id"])
            right.markdown(f"**{r_title}**  \n{r_details}")
        else:
            st.markdown(f"**{title}**  \n{details}")
        st.caption(f"Motif proposé : {args.get('reason', '—')}")

        reopened = st.session_state.setdefault("decision_reopened", {})
        if aid in reopened:
            reason = reopened.pop(aid)
            st.info("Cette proposition est de nouveau en attente côté orchestrateur : vous pouvez décider à nouveau."
                    + (f"  \nDernière tentative : {reason}" if reason else ""))
        if aid in results:
            res = results[aid]
            _show_result(res)
            if res["outcome"] != FINAL and st.button("Vérifier l'état", key=f"check_{key}"):
                checked = _recheck(aid, res)
                if checked is None:  # de nouveau en attente : actions rétablies
                    reopened[aid] = _last_reason(results.pop(aid))
                else:
                    results[aid] = {**checked, "action": action}
                st.rerun()
            return
        pending = st.session_state.get(f"confirm_{key}")
        if pending is None:
            c1, c2, _ = st.columns([1, 1, 3])
            if c1.button("Valider", key=f"approve_{key}", type="primary"):
                st.session_state[f"confirm_{key}"] = True
                st.rerun()
            if c2.button("Refuser", key=f"refuse_{key}"):
                st.session_state[f"confirm_{key}"] = False
                st.rerun()
        else:
            st.warning("Confirmer la validation de cette proposition ?" if pending
                       else "Confirmer le refus de cette proposition ?")
            c1, c2, _ = st.columns([1, 1, 3])
            if c1.button("Confirmer", key=f"confirm_btn_{key}", type="primary"):
                results[aid] = {**_decide(aid, pending, args), "action": action}  # gardée pour l'affichage
                st.session_state.pop(f"confirm_{key}", None)
                st.rerun()
            if c2.button("Annuler", key=f"cancel_{key}"):
                st.session_state.pop(f"confirm_{key}", None)
                st.rerun()


# Issue d'une demande de décision (correctifs H6, revue Codex) :
#   FINAL     : résultat établi (décision relue dans le catalogue, refus explicite, validation simulée,
#               proposition inconnue de l'orchestrateur) : plus aucune action ;
#   RETRYABLE : l'orchestrateur a refusé la requête sans rien exécuter ;
#   UNCERTAIN : la décision a PU être enregistrée (délai, panne, 5xx, échec signalé après écriture,
#               succès non relu, proposition absente de la file) : jamais de succès affiché ; nouvelle
#               vérification toujours possible ; actions rétablies si la proposition revient en file.
FINAL, RETRYABLE, UNCERTAIN = "final", "retryable", "uncertain"


def _matches(decision: dict, args: dict) -> bool:
    """Preuve d'une VALIDATION : même produit, action et remplaçant, et décision bien « approved »."""
    return decision.get("decision_status") == "approved" and \
        all(decision.get(k) == args.get(k) for k in ("product_id", "action", "replacement_id"))


def _last_reason(res: dict) -> str | None:
    return res.get("detail") or ({"unknown": "résultat inconnu", "absent": "proposition absente de la file",
                                  "not_found": "proposition inconnue de l'orchestrateur"}.get(res.get("status")))


def _verify_in_catalog(decision_id, args: dict) -> bool:
    """Preuve de persistance : la décision annoncée est relue dans le catalogue réel."""
    if not decision_id or not settings.direct_read:
        return False
    try:
        return _matches(services.get("catalog", f"/decisions/{int(decision_id)}"), args)
    except (ApiError, ValueError, TypeError):
        return False


def _decide(action_id: str, approved: bool, args: dict | None = None) -> dict:
    try:
        res = orchestrator.decide(action_id, approved)
    except Unavailable as e:
        return {"status": "unknown", "outcome": UNCERTAIN, "detail": e.detail}
    except ApiError as e:
        if e.status == 404:  # l'orchestrateur peut la remettre en file après un échec : pas une preuve
            return {"status": "not_found", "outcome": UNCERTAIN}
        return {"status": "rejected", "outcome": RETRYABLE, "detail": e.detail}
    status = res.get("status")
    if status == "executed":
        did = (res.get("result") or {}).get("decision_id")
        mode = read_orchestrator_mode()  # relu à cet instant, jamais pris dans un cache
        if mode is True:
            return {**res, "status": "executed_simulated", "outcome": FINAL}
        if mode is False and _verify_in_catalog(did, args or {}):
            return {**res, "status": "executed_verified", "outcome": FINAL}
        return {**res, "status": "executed_unverified", "outcome": UNCERTAIN, "execution_mode_mock": mode}
    if status == "refused":
        return {**res, "outcome": FINAL}
    return {**res, "outcome": UNCERTAIN}  # « failed » : l'écriture a pu aboutir avant l'erreur


def _recheck(action_id: str, previous: dict) -> dict | None:
    """Relit l'état réel. None = proposition de nouveau en attente (actions rétablies)."""
    try:
        pending_ids = {a["action_id"] for a in orchestrator.pending_actions()}
    except ApiError as e:
        return {**previous, "check_error": e.detail}
    if action_id in pending_ids:
        return None
    if previous.get("status") == "executed_unverified" and previous.get("execution_mode_mock") is False:
        mode = read_orchestrator_mode()  # jamais de relecture du catalogue réel pour un orchestrateur simulé
        if mode is False and _verify_in_catalog((previous.get("result") or {}).get("decision_id"),
                                                previous.get("action", {}).get("args", {})):
            return {**previous, "status": "executed_verified", "outcome": FINAL, "check_error": None}
    keep = previous.get("status") if previous.get("status") == "executed_unverified" else "absent"
    return {**previous, "status": keep, "outcome": UNCERTAIN, "check_error": None}


def reconcile_with_queue(pending_ids: set[str]) -> None:
    """Une proposition revenue dans la file n'est jamais bloquée par un ancien résultat incertain."""
    results = st.session_state.setdefault("decision_results", {})
    reopened = st.session_state.setdefault("decision_reopened", {})
    for aid in [a for a, r in results.items() if a in pending_ids and r.get("outcome") != FINAL]:
        reopened[aid] = _last_reason(results.pop(aid))


def _show_result(res: dict) -> None:
    status = res.get("status")
    did = (res.get("result") or {}).get("decision_id")
    if status == "executed_verified":
        st.success(f"Décision validée et vérifiée dans le catalogue (n° {did}).")
    elif status == "executed_simulated":
        st.warning(f"Décision validée, mais l'orchestrateur utilise des services simulés : rien n'a été "
                   f"écrit dans le catalogue réel (n° fictif {did}).")
    elif status == "executed_unverified":
        st.warning(f"Décision acceptée par l'orchestrateur (n° {did}), mais non vérifiée dans le catalogue : "
                   "sa persistance n'est pas prouvée. Vérifiez l'état ou l'historique.")
    elif status == "refused":
        st.info("Proposition refusée par l'orchestrateur. Ce refus n'est pas encore archivé dans l'historique "
                "du catalogue (l'orchestrateur ne le transmet pas : décision D-3).")
    elif status == "failed":
        st.warning(f"Résultat incertain : l'orchestrateur signale un échec ({res.get('detail')}), mais "
                   "l'écriture a pu aboutir avant l'erreur. Vérifiez l'état avant toute nouvelle tentative.")
    elif status == "rejected":
        st.error(f"Demande refusée par l'orchestrateur : {res.get('detail')}. Vérifiez l'état avant de réessayer.")
    elif status == "unknown":
        st.warning(f"Résultat inconnu : {res.get('detail')}. La décision a pu être enregistrée ou non. "
                   "Vérifiez l'état réel avant toute nouvelle tentative.")
    elif status == "absent":
        st.warning("Proposition absente de la file pour l'instant : l'orchestrateur peut être en train de la "
                   "traiter, l'avoir perdue ou redémarré. Ce n'est pas une preuve de décision : vérifiez à "
                   "nouveau ou consultez l'historique.")
    elif status == "not_found":
        st.warning("L'orchestrateur ne connaît plus cette proposition pour l'instant (elle peut être en cours de "
                   "traitement). Vérifiez à nouveau ou consultez l'historique.")
    else:
        st.warning(f"Résultat incertain (statut « {status} » non reconnu) : vérifiez l'état avant toute nouvelle "
                   "tentative.")
    if res.get("check_error"):
        st.error(f"Vérification impossible, orchestrateur toujours injoignable : {res['check_error']}.")
