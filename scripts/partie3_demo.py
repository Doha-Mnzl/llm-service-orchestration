"""Pile de démonstration ISOLÉE de la Partie 3 (Windows, macOS, Linux).

    python scripts/partie3_demo.py start --orchestrator-src ../orch
    python scripts/partie3_demo.py status
    python scripts/partie3_demo.py stop
    python scripts/partie3_demo.py stop-service inventory    # panne simulée d'un service
    python scripts/partie3_demo.py start-service inventory   # rétablissement

Sécurité :
- la base doit avoir un nom contenant le mot « test » (jamais lso_dev) ; elle est créée, migrée et
  remplie avec --if-empty (aucune donnée existante n'est effacée) ;
- tous les processus écoutent sur 127.0.0.1, sur des ports dérivés de --port-base (28000 par défaut),
  jamais sur ceux de la démonstration existante ;
- le catalogue est en MODE SÛR ; le jeton du client de démonstration est aléatoire et stocké hors du
  dépôt, dans un dossier d'exécution PRIVÉ ($XDG_RUNTIME_DIR ou ~/.cache, droits 700, propriétaire
  vérifié, liens symboliques refusés ; fichiers en 600 créés sans suivre de lien).
  --legacy-catalog ajoute un catalogue en mode NON SÛR
  (port base+13), réservé aux tests d'intégration ;
- l'orchestrateur (PR #1, Doha) est lancé tel quel depuis --orchestrator-src, sans modification :
  instance « réelle » (services de la pile, port base+10) et instance « mock » (base+11).

Ports (base 28000) : ventes 28001, stocks 28002, catalogue 28003, orchestrateur 28010, orchestrateur
mock 28011, catalogue legacy 28013, interface réelle 28501, interface mock 28502.
"""

import argparse
import json
import os
import re
import secrets
import signal
import stat
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN_PORTS = {8000, 8001, 8002, 8003, 8010, 8011, 8501, 8502}


NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


def run_dir(port_base: int) -> Path:
    """Dossier propre à l'utilisateur (jamais un /tmp partagé au nom prévisible)."""
    base = os.environ.get("XDG_RUNTIME_DIR") or str(Path.home() / ".cache")
    return Path(base) / "lso-partie3-demo" / str(port_base)


def secure_run_dir(port_base: int) -> Path:
    rd = run_dir(port_base)
    rd.parent.parent.mkdir(parents=True, exist_ok=True)
    for d in (rd.parent, rd):
        if not os.path.lexists(d):
            d.mkdir(mode=0o700)
        st = os.lstat(d)
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            sys.exit(f"Refus : {d} n'est pas un dossier ordinaire (lien symbolique ?).")
        if os.name != "nt" and (st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) & 0o077):
            sys.exit(f"Refus : {d} doit appartenir à l'utilisateur courant avec les droits 700.")
    return rd


def write_private(path: Path, text: str, exclusive: bool = False) -> None:
    """Écrit un fichier en 600 sans jamais suivre de lien symbolique."""
    if exclusive and os.path.lexists(path):
        os.unlink(path)  # supprime le lien lui-même, jamais sa cible
    flags = os.O_WRONLY | os.O_CREAT | NOFOLLOW | (os.O_EXCL if exclusive else os.O_TRUNC)
    with os.fdopen(os.open(path, flags, 0o600), "w", encoding="utf-8") as f:
        f.write(text)


def open_private_log(path: Path):
    return os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | NOFOLLOW, 0o600), "a", encoding="utf-8")


def ports(base: int) -> dict:
    return {"sales": base + 1, "inventory": base + 2, "catalog": base + 3, "orchestrator": base + 10,
            "orchestrator_mock": base + 11, "catalog_legacy": base + 13, "ui": base + 501, "ui_mock": base + 502}


def _healthy(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            return r.status == 200
    except OSError:
        return False


SPECS: dict = {}  # nom → commande, pour pouvoir relancer un service après une panne simulée


def _spawn(name, args, env, cwd, logs: Path) -> subprocess.Popen:
    kwargs = {"start_new_session": True} if os.name != "nt" else \
        {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}  # type: ignore[attr-defined]
    log = open_private_log(logs / f"{name}.log")
    proc = subprocess.Popen(args, cwd=cwd, env={**os.environ, **env}, stdout=log, stderr=subprocess.STDOUT,
                            **kwargs)
    SPECS[name] = {"args": args, "env": env, "cwd": str(cwd)}
    return proc


def _save(rd: Path, state: dict) -> None:
    # contient le jeton (environnement du catalogue) : lisible par l'utilisateur seul
    write_private(rd / "pids.json", json.dumps(state, indent=2))


def _kill(pid: int) -> bool:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        else:
            os.killpg(pid, signal.SIGTERM)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def database_conninfo(pg_url: str, database: str) -> str:
    """Chaîne de connexion finale ; le contrôle « test » porte sur la base RÉELLEMENT utilisée."""
    params = {**conninfo_to_dict(pg_url), "dbname": database}
    final = conninfo_to_dict(make_conninfo(**params)).get("dbname", "")
    if not re.search(r"(^|[_-])test([_-]|$)", final):
        sys.exit(f"Refus : la base « {final} » ne contient pas le mot « test » (lso_dev est interdite).")
    return make_conninfo(**params)


def db_name(conninfo: str) -> str:
    return conninfo_to_dict(conninfo).get("dbname", "?")


def service_env(db: str, token: str | None = None, legacy: bool = False) -> dict:
    """Environnement EXPLICITE d'un service : aucune variable du shell (base, mode legacy, jetons) ne
    peut le détourner vers une autre base ou un mode non sûr."""
    env = {"DATABASE_URL": db, "SALES_DATABASE_URL": db, "INVENTORY_DATABASE_URL": db, "CATALOG_DATABASE_URL": db,
           "DECISIONS_LEGACY_ORCHESTRATOR": "true" if legacy else "false",
           "CATALOG_SERVICE_TOKENS": f"demo:{token}" if token else "", "PYTHONDONTWRITEBYTECODE": "1"}
    return env


def prepare_database(pg_url: str, database: str) -> str:
    conninfo = database_conninfo(pg_url, database)
    admin = make_conninfo(**{**conninfo_to_dict(conninfo), "dbname": "postgres"})
    with psycopg.connect(admin, autocommit=True) as c:
        if not c.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database,)).fetchone():
            c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
    for argv in data_commands():  # connexion par l'environnement : jamais visible dans la liste des processus
        subprocess.run(argv, cwd=ROOT, check=True, env={**os.environ, "DATABASE_URL": conninfo})
    return conninfo


def data_commands() -> list[list[str]]:
    return [[sys.executable, "-m", "data.migrate"],
            [sys.executable, "-m", "data.generate", "--seed", "42", "--if-empty"],
            [sys.executable, "-m", "data.verify"]]


def _markers(name: str, port: int) -> list[str]:
    return ["--server.port" if name.startswith("ui") else "--port", str(port)]


def belongs(pid: int, markers: list[str]) -> bool:
    """Vrai seulement si le processus existe et que sa ligne de commande porte les marqueurs attendus
    (évite de tuer un PID recyclé après un plantage ou un redémarrage)."""
    try:
        if os.path.exists(f"/proc/{pid}/cmdline"):
            argv = Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace").split("\0")
        elif os.name == "nt":
            out = subprocess.run(["powershell", "-NoProfile", "-Command",
                                  f"(Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}').CommandLine"],
                                 capture_output=True, text=True).stdout
            argv = out.split()
        else:
            argv = subprocess.run(["ps", "-p", str(int(pid)), "-o", "command="], capture_output=True,
                                  text=True).stdout.split()
    except OSError:
        return False
    return bool(argv) and all(m in argv for m in markers)


def start(a) -> None:
    p = ports(a.port_base)
    if FORBIDDEN_PORTS & set(p.values()):
        sys.exit("Refus : ces ports chevauchent la démonstration existante.")
    orch = None
    if a.orchestrator_src:  # vérifié AVANT tout démarrage : pas de pile à moitié lancée
        orch = Path(a.orchestrator_src).resolve()
        if not (orch / "orchestrator" / "main.py").is_file():
            sys.exit(f"Refus : {orch} ne contient pas orchestrator/main.py (branche feature/orchestrator).")
    rd = secure_run_dir(a.port_base)
    if (rd / "pids.json").exists():
        sys.exit(f"Une pile semble déjà lancée ({rd}). Faire « stop » d'abord.")
    if not os.path.lexists(rd / "logs"):
        (rd / "logs").mkdir(mode=0o700)
    if os.path.islink(rd / "logs"):
        sys.exit("Refus : le dossier des journaux est un lien symbolique.")
    names = ["sales", "inventory", "catalog", "ui", "ui_mock"]
    if a.legacy_catalog:
        names.append("catalog_legacy")
    if orch:
        names.extend(["orchestrator", "orchestrator_mock"])
    for name in names:
        with socket.socket() as probe:
            if os.name != "nt":
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", p[name]))
            except OSError as exc:
                sys.exit(f"{name} : port {p[name]} indisponible ({exc}).")
    db = prepare_database(a.pg_url, a.database)
    token = secrets.token_urlsafe(32)
    token_file = rd / "catalog_token"
    write_private(token_file, token, exclusive=True)

    py, host, logs, children = sys.executable, "127.0.0.1", rd / "logs", {}
    uv = [py, "-m", "uvicorn", "--host", host]
    services = {"SALES_URL": f"http://{host}:{p['sales']}", "INVENTORY_URL": f"http://{host}:{p['inventory']}",
                "CATALOG_URL": f"http://{host}:{p['catalog']}"}
    st = [py, "-m", "streamlit", "run", "app.py", "--server.headless", "true", "--server.address", host]
    try:
        children["sales"] = _spawn("sales", [*uv, "services.sales.main:app", "--port", str(p["sales"])],
                                   service_env(db), ROOT, logs)
        children["inventory"] = _spawn("inventory", [*uv, "services.inventory.main:app", "--port", str(p["inventory"])],
                                       service_env(db), ROOT, logs)
        children["catalog"] = _spawn("catalog", [*uv, "services.catalog.main:app", "--port", str(p["catalog"])],
                                     service_env(db, token), ROOT, logs)
        if a.legacy_catalog:
            children["catalog_legacy"] = _spawn("catalog_legacy", [*uv, "services.catalog.main:app", "--port",
                                                                   str(p["catalog_legacy"])],
                                                service_env(db, legacy=True), ROOT, logs)
        if orch:
            children["orchestrator"] = _spawn("orchestrator", [*uv, "orchestrator.main:app", "--port",
                                                               str(p["orchestrator"])],
                                          {"USE_MOCK_SERVICES": "false", "LLM_PROVIDER": "fake", **services}, orch, logs)
            children["orchestrator_mock"] = _spawn("orchestrator_mock", [*uv, "orchestrator.main:app", "--port",
                                                                         str(p["orchestrator_mock"])],
                                               {"USE_MOCK_SERVICES": "true", "LLM_PROVIDER": "fake"}, orch, logs)
        children["ui"] = _spawn("ui", [*st, "--server.port", str(p["ui"])],
                            {**services, "DASHBOARD_DIRECT_READ": "true",
                             "ORCHESTRATOR_URL": f"http://{host}:{p['orchestrator']}"}, ROOT / "frontend", logs)
        # interface mock : aucune lecture directe des vrais services (pas de mélange réel / simulé)
        children["ui_mock"] = _spawn("ui_mock", [*st, "--server.port", str(p["ui_mock"])],
                                 {"DASHBOARD_DIRECT_READ": "false",
                                  "ORCHESTRATOR_URL": f"http://{host}:{p['orchestrator_mock']}"},
                                 ROOT / "frontend", logs)
        deadline = time.monotonic() + 40
        while True:
            ready = all(_healthy(url) for url in _health_urls(p, children))
            for name, proc in children.items():
                if proc.poll() is not None:
                    sys.exit(f"{name} : arrêt pendant le démarrage (voir {logs / (name + '.log')}).")
            if ready:
                break
            if time.monotonic() >= deadline:
                sys.exit(f"Délai de démarrage dépassé (voir {logs}).")
            time.sleep(0.5)
        pids = {name: proc.pid for name, proc in children.items()}
        _save(rd, {"pids": pids, "ports": p, "database": db, "specs": SPECS})
    except BaseException:
        for proc in children.values():
            if proc.poll() is None:
                _kill(proc.pid)
        raise
    status(a)
    print(f"\nJeton du client de démonstration (mode sûr) : {token_file}")


def _health_urls(p: dict, pids: dict) -> list[str]:
    urls = []
    for name in pids:
        path = "/_stcore/health" if name.startswith("ui") else "/health"
        urls.append(f"http://127.0.0.1:{p[name]}{path}")
    return urls


def status(a) -> None:
    rd = run_dir(a.port_base)
    if not (rd / "pids.json").exists():
        print("Aucune pile de démonstration lancée.")
        return
    state = json.loads((rd / "pids.json").read_text(encoding="utf-8"))
    print(f"Base : {db_name(state['database'])}   journaux : {rd / 'logs'}")
    for name, pid in state["pids"].items():
        port = state["ports"][name]
        path = "/_stcore/health" if name.startswith("ui") else "/health"
        ok = pid and belongs(pid, _markers(name, port)) and _healthy(f"http://127.0.0.1:{port}{path}")
        print(f"  {name:18} http://127.0.0.1:{port:<6} pid {pid:<8} {'OK' if ok else 'INDISPONIBLE'}")


def stop(a) -> None:
    rd = run_dir(a.port_base)
    if not (rd / "pids.json").exists():
        print("Aucune pile de démonstration lancée.")
        return
    state = json.loads((rd / "pids.json").read_text(encoding="utf-8"))
    for name, pid in state["pids"].items():
        ours = pid and belongs(pid, _markers(name, state["ports"][name]))
        print(f"  {'arrêté' if ours and _kill(pid) else 'ignoré (déjà arrêté ou PID réutilisé)'} : {name}")
    (rd / "pids.json").unlink()
    print(f"Pile arrêtée. La base « {db_name(state['database'])} » est conservée.")


def _load_service_state(a) -> tuple[Path, dict]:
    rd = run_dir(a.port_base)
    if not (rd / "pids.json").exists():
        sys.exit("Aucune pile de démonstration lancée (faire « start » d'abord).")
    state = json.loads((rd / "pids.json").read_text(encoding="utf-8"))
    if a.service not in state.get("specs", {}):
        sys.exit(f"Service inconnu dans cette pile : « {a.service} » (disponibles : {', '.join(state['specs'])}).")
    return rd, state


def stop_service(a) -> None:
    rd, state = _load_service_state(a)
    pid = state["pids"].get(a.service)
    ours = pid and belongs(pid, _markers(a.service, state["ports"][a.service]))
    print(f"{a.service} : {'arrêté (panne simulée)' if ours and _kill(pid) else 'déjà arrêté'}")
    state["pids"][a.service] = 0
    _save(rd, state)


def start_service(a) -> None:
    rd, state = _load_service_state(a)
    spec, port = state["specs"][a.service], state["ports"][a.service]
    path = "/_stcore/health" if a.service.startswith("ui") else "/health"
    tracked = state["pids"].get(a.service)
    if tracked and belongs(tracked, _markers(a.service, port)):
        sys.exit(f"{a.service} : l'instance suivie (pid {tracked}) est toujours active ; faire stop-service d'abord.")
    if _healthy(f"http://127.0.0.1:{port}{path}"):
        sys.exit(f"{a.service} : le port {port} répond déjà (instance non suivie) ; relance refusée.")
    proc = _spawn(a.service, spec["args"], spec["env"], Path(spec["cwd"]), rd / "logs")
    deadline = time.time() + 20
    while time.time() < deadline and proc.poll() is None and not _healthy(f"http://127.0.0.1:{port}{path}"):
        time.sleep(0.3)
    if proc.poll() is not None or not _healthy(f"http://127.0.0.1:{port}{path}"):
        _kill(proc.pid)  # le nouvel enfant n'a pas démarré : rien n'est publié
        state["pids"][a.service] = 0
        _save(rd, state)
        sys.exit(f"{a.service} : échec du redémarrage (voir {rd / 'logs' / (a.service + '.log')}).")
    state["pids"][a.service], state["specs"][a.service] = proc.pid, spec  # publié après confirmation
    _save(rd, state)
    print(f"{a.service} : relancé sur http://127.0.0.1:{port} (pid {proc.pid})")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("action", choices=["start", "status", "stop", "stop-service", "start-service"])
    p.add_argument("service", nargs="?", help="pour stop-service / start-service (ex. inventory)")
    p.add_argument("--port-base", type=int, default=28000)
    p.add_argument("--pg-url", default="postgresql://lso:lso@localhost:5433", help="serveur PostgreSQL, sans base")
    p.add_argument("--database", default="lso_demo_test")
    p.add_argument("--orchestrator-src", help="dossier de la branche feature/orchestrator (PR #1)")
    p.add_argument("--legacy-catalog", action="store_true", help="ajoute un catalogue en mode NON SÛR (tests)")
    a = p.parse_args()
    if a.action in ("stop-service", "start-service") and not a.service:
        p.error("préciser le service, ex. : stop-service inventory")
    {"start": start, "status": status, "stop": stop, "stop-service": stop_service,
     "start-service": start_service}[a.action](a)


if __name__ == "__main__":
    main()
